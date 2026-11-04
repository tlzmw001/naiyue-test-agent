"""Maintenance tools come from a current verified executor, not the task pin."""
import json
from types import SimpleNamespace
import pytest
from lifecycle_helpers import template
from test_offline_operations import setup
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_current_maintenance_probe_imports_both_real_implementations():
    from naiyue_test_agent.cloud_runner.maintenance_probe import report
    value=report()
    assert value['maintenance_protocol']==1 and value['raw_backup'] and value['raw_restore']


def test_explicit_maintenance_receipt_is_independent_of_old_task_image(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.maintenance import select_maintenance_template
    from naiyue_test_agent.cloud_install.store import write
    import naiyue_test_agent.cloud_install.maintenance as module
    settings,cloud,task,instance=setup(tmp_path);current=template(runner_release_id='maintenance-r3',image_id='sha256:'+'c'*64)
    receipt=tmp_path/'maintenance.json';write(receipt,current.model_dump());settings=settings.model_copy(update={'maintenance_receipt':receipt})
    commands=[]
    def daemon(settings,args):
        commands.append(args)
        if args[:2]==['image','inspect']:return json.dumps([dict(Id=current.image_id)]).encode()
        return json.dumps(dict(maintenance_protocol=1,raw_backup=True,raw_restore=True,uid=current.uid,gid=current.gid)).encode()
    monkeypatch.setattr(module,'_run',daemon)
    owned=SimpleNamespace(settings=cloud,snapshot={'meta':{}},instance_id=instance)
    assert select_maintenance_template(settings,owned)==current
    assert current.image_id in commands[-1] and 'naiyue_test_agent.cloud_runner.maintenance_probe' in commands[-1]
    assert task.runner_release_id not in commands[-1]


def test_old_image_without_restore_module_is_rejected_before_any_task_mount(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.maintenance import select_maintenance_template
    import naiyue_test_agent.cloud_install.maintenance as module
    settings,cloud,_,instance=setup(tmp_path);commands=[]
    def daemon(settings,args):
        commands.append(args)
        if args[:2]==['image','inspect']:return json.dumps([dict(Id=template().image_id)]).encode()
        raise CloudError('upgrade_backup_failed')
    monkeypatch.setattr(module,'_run',daemon)
    with pytest.raises(CloudError,match='maintenance_template_unavailable'):
        select_maintenance_template(settings,SimpleNamespace(settings=cloud,snapshot={'meta':{}},instance_id=instance))
    assert all('--mount' not in args for args in commands)


def test_maintenance_executor_must_match_configured_build_identity(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.maintenance import select_maintenance_template
    import naiyue_test_agent.cloud_install.maintenance as module
    settings,cloud,_,instance=setup(tmp_path);calls=[]
    settings=settings.model_copy(update={'build':settings.build.model_copy(update={'uid':settings.build.uid+1})})
    monkeypatch.setattr(module,'_run',lambda *a:calls.append(a))
    with pytest.raises(CloudError,match='maintenance_template_identity_mismatch'):
        select_maintenance_template(settings,SimpleNamespace(settings=cloud,snapshot={'meta':{}},instance_id=instance))
    assert calls==[]
