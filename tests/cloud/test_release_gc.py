"""Offline GC withdraws unused ready records and never prunes referenced resources."""
import hashlib,json
from uuid import uuid4
import pytest
from lifecycle_helpers import template
from test_release_updates import release
from test_offline_operations import setup
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_install.store import UpdateStore,directory,write


def fixture(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_install.offline as offline
    import naiyue_test_agent.cloud_install.reclaim as module
    settings,cloud,task,instance=setup(tmp_path);old=b'old wheel';sha=hashlib.sha256(old).hexdigest()
    target=release().model_copy(update={'wheels':{'linux-arm64':release().wheels['linux-arm64'].model_copy(update={'sha256':sha})}})
    t=template(runner_release_id='r2',wheel_sha256=sha,image_id='sha256:'+'2'*64)
    newer=template(runner_release_id='r3',image_id='sha256:'+'3'*64)
    with open_cloud_runtime(cloud) as r:
        r.releases.register(dict(command_id=str(uuid4()),release=target.model_dump(),template=t.model_dump()))
        r.releases.register(dict(command_id=str(uuid4()),release=release('r3').model_dump(),template=newer.model_dump()))
        r.releases.activate(dict(command_id=str(uuid4()),release_id='r3',template_id=t.template_id,expected_revision=0))
    store=UpdateStore(settings);job=str(uuid4());store.create(job,'r2')
    store.save(job,phase='completed',image_id=t.image_id,build_input_sha256='a'*64)
    write(directory(settings.work_dir/job)/'receipt.json',t.model_dump())
    cache=directory(directory(settings.work_dir/'artifacts')/sha);(cache/'agent.whl').write_bytes(old)
    tags=[settings.build.image_tag+'-'+job.replace('-','')];images={t.image_id};calls=[];lose_ack=[]
    def daemon(settings,args):
        calls.append(args)
        if args[:2]==['image','ls']:return b'\n'.join(json.dumps(dict(ID=v)).encode() for v in sorted(images))
        if args[:2]==['image','inspect']:return json.dumps([dict(Id=args[-1],RepoTags=tags,Config={'Labels':{'org.naiyue.build-input':'a'*64}})]).encode()
        if args[:2]==['container','ls']:return b''
        if args[:2]==['image','rm']:
            images.discard(args[-1])
            if lose_ack:lose_ack.pop();raise CloudError('upgrade_backup_failed')
            return b''
        raise AssertionError(args)
    monkeypatch.setattr(offline,'ensure_no_writers',lambda *a:None);monkeypatch.setattr(module,'_run',daemon)
    return settings,cloud,task,t,sha,images,tags,calls,lose_ack


def preview(settings):
    from naiyue_test_agent.cloud_install.reclaim import prepare_gc
    ident=str(uuid4());store=UpdateStore(settings);store.create_operation(ident,'gc',{})
    value=prepare_gc(settings,ident,store)
    return ident,store,value


def test_unused_image_cache_and_ready_record_are_retired_after_confirmation(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.reclaim import apply_gc
    settings,cloud,task,t,sha,images,_,calls,_=fixture(tmp_path,monkeypatch)
    ident,store,value=preview(settings)
    assert {v['kind'] for v in value['candidates']}=={'image','cache'}
    assert images and (settings.work_dir/'artifacts'/sha).exists()
    store.save(ident,approved_digest=value['plan_sha256'])
    assert apply_gc(settings,ident,store)['phase']=='completed'
    assert not images and not (settings.work_dir/'artifacts'/sha).exists()
    assert all('--force' not in args for args in calls)
    with open_cloud_runtime(cloud) as r:
        assert r.releases.active('python-tests')['release_id']=='r3'
        with pytest.raises(CloudError,match='release_not_ready'):r.releases.get('r2','python-tests')
        with pytest.raises(CloudError,match='release_not_ready'):r.releases.validate_template(t)


def test_stopped_task_reference_and_foreign_image_tag_are_protected(tmp_path,monkeypatch):
    settings,cloud,task,t,sha,_,tags,_,_=fixture(tmp_path,monkeypatch)
    with open_cloud_runtime(cloud) as r:
        current=r.registry.get_task(task.task_id);r.registry.save_task_cas(current.model_copy(update={'runner_release_id':'r2',
            'template_snapshot':t,'template_digest':t.fingerprint}),expected_revision=current.revision,expected_generation=current.generation,revoked_at=0)
    _,_,value=preview(settings)
    assert not any(v['kind']=='image' for v in value['candidates'])
    assert not any(v['kind']=='cache' and v['name']==sha for v in value['candidates'])
    assert any(v['reason']=='task_or_active_or_backup' for v in value['protected'])


def test_reference_changes_invalidate_the_preview_without_deleting(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.reclaim import apply_gc
    settings,cloud,_,_,sha,images,_,calls,_=fixture(tmp_path,monkeypatch)
    ident,store,value=preview(settings);store.save(ident,approved_digest=value['plan_sha256'])
    with open_cloud_runtime(cloud) as r:r.releases.activate(dict(command_id=str(uuid4()),release_id='r2',template_id='python-tests',expected_revision=1))
    with pytest.raises(CloudError,match='gc_plan_changed'):apply_gc(settings,ident,store)
    assert images and (settings.work_dir/'artifacts'/sha).exists() and not any(args[:2]==['image','rm'] for args in calls)


def test_lost_delete_ack_blocks_startup_and_new_jobs_until_original_gc_finishes(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.reclaim import apply_gc
    settings,cloud,_,_,sha,images,_,_,lose_ack=fixture(tmp_path,monkeypatch)
    ident,store,value=preview(settings);store.save(ident,approved_digest=value['plan_sha256']);lose_ack.append(True)
    with pytest.raises(CloudError):apply_gc(settings,ident,store)
    assert not images
    with pytest.raises(CloudError,match='cloud_gc_guard'):
        with open_cloud_runtime(cloud):pytest.fail('incomplete GC opened')
    with pytest.raises(CloudError,match='update_gc_pending'):store.create(str(uuid4()),'r2')
    assert apply_gc(settings,ident,store)['phase']=='completed'
    with open_cloud_runtime(cloud):pass


def test_foreign_tag_never_becomes_a_delete_candidate(tmp_path,monkeypatch):
    settings,_,_,_,_,images,tags,calls,_=fixture(tmp_path,monkeypatch);tags.append('another-deployment:keep')
    _,_,value=preview(settings)
    assert not any(v['kind']=='image' for v in value['candidates'])
    assert images and not any(args[:2]==['image','rm'] for args in calls)


def test_guard_publication_failure_before_deleting_releases_the_job_fence(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.reclaim import apply_gc
    import naiyue_test_agent.cloud_install.reclaim as module
    settings,cloud,_,_,_,images,_,_,_=fixture(tmp_path,monkeypatch);ident,store,value=preview(settings)
    store.save(ident,approved_digest=value['plan_sha256']);original=module.write
    def fail(path,value):
        if path==cloud.data_dir/'gc-guard.json':raise OSError('controlled publication failure')
        return original(path,value)
    monkeypatch.setattr(module,'write',fail)
    with pytest.raises(OSError):apply_gc(settings,ident,store)
    assert images
    assert store.create(str(uuid4()),'r3')['phase']=='queued'
    with open_cloud_runtime(cloud):pass


def test_resume_after_physical_completion_never_redeletes_a_new_image(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.reclaim import apply_gc
    import naiyue_test_agent.cloud_install.reclaim as module
    settings,cloud,_,t,_,images,_,calls,_=fixture(tmp_path,monkeypatch);ident,store,value=preview(settings)
    store.save(ident,approved_digest=value['plan_sha256']);original=module._finish_guards
    def fail(*a):raise OSError('controlled finalization failure')
    monkeypatch.setattr(module,'_finish_guards',fail)
    with pytest.raises(OSError):apply_gc(settings,ident,store)
    assert store.read(ident)['step']=='finalizing' and not images
    images.add(t.image_id);monkeypatch.setattr(module,'_finish_guards',original)
    apply_gc(settings,ident,store)
    assert images and len([a for a in calls if a[:2]==['image','rm']])==1


def test_corrupt_new_backup_does_not_displace_the_last_verified_backup(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.backup import create_backup,_manifest
    settings,_,_,_,_,_,_,_,_=fixture(tmp_path,monkeypatch);settings=settings.model_copy(update={'keep_backups':1})
    old=str(uuid4());new=str(uuid4());create_backup(settings,old);create_backup(settings,new)
    _,payload=_manifest(settings,new);(payload/'management/cloud.sqlite').write_bytes(b'corrupt')
    _,_,value=preview(settings)
    assert not any(v['kind']=='backup' and v['name']==old for v in value['candidates'])
    assert any(v['kind']=='backup' and v['name']==new and 'integrity' in v['reason'] for v in value['protected'])


@pytest.mark.parametrize('phase,protected',[('prepared',True),('completed',False)])
def test_only_unfinished_restore_keeps_backup_beyond_retention(tmp_path,monkeypatch,phase,protected):
    from naiyue_test_agent.cloud_install.backup import create_backup
    settings,_,_,_,_,_,_,_,_=fixture(tmp_path,monkeypatch);settings=settings.model_copy(update={'keep_backups':1})
    old=str(uuid4());new=str(uuid4());create_backup(settings,old);create_backup(settings,new)
    store=UpdateStore(settings);job=str(uuid4());store.create_operation(job,'restore',dict(backup_id=old));store.save(job,phase=phase)
    _,_,value=preview(settings)
    assert any(v['kind']=='backup' and v['name']==old for v in value['candidates']) is not protected


def test_redacted_deleted_upgrade_with_raw_backup_is_reported_and_protected(tmp_path,monkeypatch):
    from naiyue_test_agent.storage.cloud.releases import put
    from naiyue_test_agent.contracts.cloud_tasks import CommandReceipt
    import time
    settings,cloud,task,_,_,images,_,_,_=fixture(tmp_path,monkeypatch);ident=str(uuid4())
    directory(directory(settings.work_dir/'backups')/ident)
    # Exact durable post-delete contract: old command exists, sensitive result
    # was redacted, and private raw upgrade backup remains outside task root.
    receipt=CommandReceipt(command_id=ident,resource_id=task.task_id,operation='release:upgrade',request_digest='a'*64,
        status='failed',http_status=410,result=dict(delete_command_id=str(uuid4())),created_at=time.time(),updated_at=time.time())
    with open_cloud_runtime(cloud) as r:
        with r.database.transaction(write=True) as db:
            db.execute('INSERT INTO commands VALUES(?,?,?,?)',(ident,receipt.request_digest,task.task_id,receipt.model_dump_json()))
    _,_,value=preview(settings)
    assert images and not value['candidates']
    assert any(v['kind']=='upgrade_backup' and v['reason']=='unresolved_reference' and v['name']==ident for v in value['protected'])
