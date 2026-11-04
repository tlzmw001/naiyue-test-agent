"""Offline ownership and both writer namespaces fence destructive maintenance."""
import json
from uuid import uuid4
import pytest
from test_update_installer import inputs
from lifecycle_helpers import config,register,template
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def setup(tmp_path):
    settings,_=inputs(tmp_path);cloud=config(tmp_path)
    with open_cloud_runtime(cloud) as r:
        task=register(r);task=task.model_copy(update={'lifecycle':'stopped','environment_phase':'absent'})
        r.registry.save_task_cas(task,expected_revision=task.revision,expected_generation=task.generation,revoked_at=0)
        r.releases.maintenance(dict(command_id=str(uuid4()),action='enter'));instance=r.instance_id
    path=tmp_path/'cloud-settings.json';path.write_text(cloud.model_dump_json());path.chmod(0o600)
    settings.connection_file.write_text(json.dumps(dict(endpoint=cloud.origin,owner_authority=cloud.owner_listen,
        owner_token='fixture-only',instance_id=instance)))
    return settings.model_copy(update={'cloud_settings_file':path,'task_data_dir':cloud.data_dir,'recovery_dir':tmp_path/'recovery'}),cloud,task,instance


def test_online_owner_prevents_offline_maintenance_before_daemon_io(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.offline import offline
    import naiyue_test_agent.cloud_install.offline as module
    settings,cloud,_,_=setup(tmp_path);calls=[]
    monkeypatch.setattr(module,'ensure_no_writers',lambda *a:calls.append(a))
    with open_cloud_runtime(cloud):
        with pytest.raises(CloudError,match='cloud_in_use'):
            with offline(settings):pytest.fail('concurrent owner accepted')
    assert calls==[]


@pytest.mark.parametrize('label',['nyt-instance','org.naiyue.upgrade-instance'])
def test_old_task_or_maintenance_container_blocks_all_offline_work(tmp_path,monkeypatch,label):
    from naiyue_test_agent.cloud_install.offline import offline
    import naiyue_test_agent.cloud_install.offline as module
    settings,_,_,_=setup(tmp_path);queries=[]
    def daemon(settings,args):
        if args[0]=='info':return json.dumps(dict(ID=settings.build.backend_id)).encode()
        queries.append(args)
        return b'{"ID":"'+b'a'*64+b'"}\n' if any('label='+label+'=' in word for word in args) else b''
    monkeypatch.setattr(module,'_run',daemon)
    with pytest.raises(CloudError,match='offline_writer_present'):
        with offline(settings):pytest.fail('physical writer accepted')
    assert queries


def test_stopped_registry_is_read_under_exclusive_owner_and_maintenance(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.offline import offline
    import naiyue_test_agent.cloud_install.offline as module
    settings,cloud,task,instance=setup(tmp_path)
    monkeypatch.setattr(module,'ensure_no_writers',lambda *a:None)
    with offline(settings) as owned:
        assert owned.instance_id==instance and owned.snapshot['tasks'][0]['task_id']==task.task_id
        with pytest.raises(CloudError,match='cloud_in_use'):
            with open_cloud_runtime(cloud):pytest.fail('second owner accepted')


@pytest.mark.parametrize('field',['work_dir','recovery_dir'])
def test_existing_directory_alias_is_not_accepted_as_a_separate_root(tmp_path,monkeypatch,field):
    import naiyue_test_agent.cloud_install.offline as module
    settings,cloud,_,_=setup(tmp_path);alias=tmp_path/'alias';alias.mkdir()
    original=module.directory_scope
    monkeypatch.setattr(module,'directory_scope',lambda p:original(cloud.data_dir) if p==alias else original(p))
    value=settings.model_copy(update={field:alias/'new-child'})
    with pytest.raises(CloudError,match='offline_roots_overlap'):module.configuration(value)
    assert not (alias/'new-child').exists()
