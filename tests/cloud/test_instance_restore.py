"""Full backups restore into guarded new roots and never open two live copies."""
from uuid import uuid4
import pytest
from test_instance_backup import fixture
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_install.store import UpdateStore
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def prepared(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.backup import create_backup
    import naiyue_test_agent.cloud_install.restore as module
    from naiyue_test_agent.cloud_runner.restore_backup import restore
    settings,cloud,task,instance=fixture(tmp_path,monkeypatch);backup_id=str(uuid4());create_backup(settings,backup_id)
    def isolated(settings,result,source,destination,request_file):
        from naiyue_test_agent.cloud_install.store import read
        return restore(source,destination/'workspace',destination/'state',read(request_file,8*1024*1024),
            max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files)
    monkeypatch.setattr(module,'restore_container',isolated)
    return settings,cloud,task,instance,backup_id


def prepare_one(settings,backup_id):
    from naiyue_test_agent.cloud_install.restore import prepare_restore
    ident=str(uuid4());store=UpdateStore(settings);store.create_operation(ident,'restore',dict(backup_id=backup_id))
    return ident,prepare_restore(settings,ident,backup_id,store)


def test_new_root_rebinds_input_and_requires_explicit_single_root_activation(tmp_path,monkeypatch):
    from pathlib import Path
    from naiyue_test_agent.cloud_install.restore import activate_restore
    settings,cloud,task,instance,backup_id=prepared(tmp_path,monkeypatch)
    ident,value=prepare_one(settings,backup_id);target=Path(value['restore_root']);new=cloud.model_copy(update={'data_dir':target})
    with pytest.raises(CloudError,match='cloud_restore_guard'):
        with open_cloud_runtime(new):pytest.fail('pending root opened')
    assert activate_restore(settings,ident,ident)['phase']=='completed'
    with pytest.raises(CloudError,match='cloud_restore_guard'):
        with open_cloud_runtime(cloud):pytest.fail('superseded root opened')
    with open_cloud_runtime(new) as r:
        assert r.instance_id==instance
        current=r.registry.get_task(task.task_id);assert current.lifecycle=='stopped' and current.generation==task.generation
        assert r.inputs.get_input(task.input_id).input_id==task.input_id
        assert r.inputs.repository.input(task.input_id).archive_identity!=__import__('json').loads((cloud.data_dir/'inputs'/task.input_id/'input.json').read_text())['archive_identity']
    assert (cloud.data_dir/'cloud.sqlite').exists()


def test_one_backup_cannot_open_two_restored_roots(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.restore import activate_restore
    settings,_,_,_,backup_id=prepared(tmp_path,monkeypatch)
    first,_=prepare_one(settings,backup_id);second,_=prepare_one(settings,backup_id)
    activate_restore(settings,first,first)
    with pytest.raises(CloudError,match='restore_source_already_superseded'):activate_restore(settings,second,second)


def test_wrong_confirmation_never_retires_original_root(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.restore import activate_restore
    settings,cloud,_,_,backup_id=prepared(tmp_path,monkeypatch);ident,_=prepare_one(settings,backup_id)
    with pytest.raises(CloudError,match='restore_confirmation_required'):activate_restore(settings,ident,str(uuid4()))
    with open_cloud_runtime(cloud):pass


@pytest.mark.parametrize('relative',['tasks/{task}/workspace/file','tasks/{task}/workspace'])
def test_prepared_target_permission_change_is_rejected_before_retiring_source(tmp_path,monkeypatch,relative):
    from pathlib import Path
    from naiyue_test_agent.cloud_install.restore import activate_restore
    settings,cloud,task,_,backup_id=prepared(tmp_path,monkeypatch);ident,value=prepare_one(settings,backup_id)
    path=Path(value['restore_root'])/relative.format(task=task.task_id)
    path.chmod(path.stat().st_mode^0o010)
    with pytest.raises(CloudError,match='backup_invalid'):
        activate_restore(settings,ident,ident)
    with open_cloud_runtime(cloud):pass


def test_real_history_status_and_archive_survive_whole_backup_and_restore(tmp_path,monkeypatch):
    from pathlib import Path
    import hashlib,time
    from test_history_ingress import batch
    from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
    from naiyue_test_agent.contracts.cloud_events import EventBatch
    from naiyue_test_agent.cloud_install.backup import create_backup
    from naiyue_test_agent.cloud_install.restore import activate_restore
    import naiyue_test_agent.cloud_install.restore as module
    from naiyue_test_agent.cloud_runner.restore_backup import restore
    from naiyue_test_agent.cloud_install.store import read
    settings,cloud,task,_=fixture(tmp_path,monkeypatch)
    packet=batch(task);data=b'restored report\x00bytes'
    artifact=ArtifactRecord(command_id=str(uuid4()),artifact_id=str(uuid4()),relative_name='report.txt',task_id=task.task_id,
        project_id=task.project_id,input_id=task.input_id,generation=task.generation,captured_at=time.time(),size=len(data),sha256=hashlib.sha256(data).hexdigest())
    with open_cloud_runtime(cloud) as r:
        r._history(task.task_id).append(EventBatch.model_validate(packet))
        assert r.registry.history_status(task.task_id)=='ready'
        transfer=r.artifacts.repository.prepare(artifact,cloud.artifact_limits)
        stage=r.artifacts.store.staging(artifact);(stage/'bytes').write_bytes(data);(stage/'bytes').chmod(0o600)
        original_identity=r.artifacts.store.publish(artifact)
        r.artifacts.repository.available(transfer,original_identity)
    backup_id=str(uuid4());create_backup(settings,backup_id)
    def isolated(settings,result,source,destination,request_file):
        return restore(source,destination/'workspace',destination/'state',read(request_file,8*1024*1024),
            max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files)
    monkeypatch.setattr(module,'restore_container',isolated)
    ident,value=prepare_one(settings,backup_id);activate_restore(settings,ident,ident)
    new=cloud.model_copy(update={'data_dir':Path(value['restore_root'])})
    with open_cloud_runtime(new) as r:
        page=r.history_page(task.task_id)
        assert page['last_seq']==1 and page['events'][0]['payload']==packet['events'][0]['payload']
        assert r.registry.history_status(task.task_id)=='ready'
        restored=r.artifacts.repository.get(task.task_id,artifact.artifact_id)
        assert restored.blob_identity!=original_identity
        with r.artifacts.store.open(restored) as stream:assert stream.read()==data


def test_replacing_source_root_with_equal_clone_cannot_enable_a_second_live_copy(tmp_path,monkeypatch):
    import shutil
    from pathlib import Path
    from naiyue_test_agent.cloud_install.restore import activate_restore
    settings,cloud,_,_,backup_id=prepared(tmp_path,monkeypatch);ident,value=prepare_one(settings,backup_id)
    original=tmp_path/'original-away';cloud.data_dir.rename(original);shutil.copytree(original,cloud.data_dir)
    with pytest.raises(CloudError,match='restore_source_changed'):activate_restore(settings,ident,ident)
    assert not (original/'restore-guard.json').exists() and not (cloud.data_dir/'restore-guard.json').exists()
    with open_cloud_runtime(cloud.model_copy(update={'data_dir':original})):pass
    with pytest.raises(CloudError,match='cloud_restore_guard'):
        with open_cloud_runtime(cloud.model_copy(update={'data_dir':Path(value['restore_root'])})):pytest.fail('target activated')
