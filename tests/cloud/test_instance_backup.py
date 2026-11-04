"""Whole-instance backup keeps management snapshots separate from task raw bytes."""
import json,sqlite3
from uuid import uuid4
import pytest
from test_offline_operations import setup
from naiyue_test_agent.application.cloud.directories import TaskDirectories
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from lifecycle_helpers import template


def fixture(tmp_path,monkeypatch):
    settings,cloud,task,instance=setup(tmp_path)
    TaskDirectories(cloud.data_dir).prepare(task,template())
    directory=cloud.data_dir/'tasks'/task.task_id
    (directory/'workspace/file').write_bytes(b'binary\x00bytes')
    (directory/'state/session.sqlite').write_bytes(b'opaque, never opened by management')
    (directory/'state/session.sqlite-wal').write_bytes(b'WAL bytes')
    import naiyue_test_agent.cloud_install.offline as offline
    import naiyue_test_agent.cloud_install.backup as backup
    monkeypatch.setattr(offline,'ensure_no_writers',lambda *a:None)
    # This fixture controls the isolated executor; its real selection/probe is
    # independently exercised in test_maintenance_template.
    monkeypatch.setattr(backup,'select_maintenance_template',lambda *a:template())
    def isolated(settings,result,destination,instance):
        from naiyue_test_agent.cloud_runner.upgrade_backup import backup as raw
        from naiyue_test_agent.contracts.cloud_events import digest
        request={key:result['reservation'][key] for key in ('command_id','task_id','generation')}
        request['reservation_sha256']=digest(result['reservation'])
        source=settings.task_data_dir/'tasks'/request['task_id']
        return raw(source/'workspace',source/'state',destination,request,max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files)
    monkeypatch.setattr(backup,'backup_container',isolated)
    return settings,cloud,task,instance


def test_instance_backup_is_verified_idempotent_and_never_opens_task_sqlite(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.backup import create_backup,verify_backup,list_backups
    settings,cloud,task,instance=fixture(tmp_path,monkeypatch);opened=[];original=sqlite3.connect
    def connect(database,*a,**kw):opened.append(str(database));return original(database,*a,**kw)
    monkeypatch.setattr(sqlite3,'connect',connect)
    ident=str(uuid4());first=create_backup(settings,ident)
    assert create_backup(settings,ident)==first
    manifest,payload=verify_backup(settings,ident)
    assert manifest['instance_id']==instance and manifest['tasks'][0]['task_id']==task.task_id
    assert not any('/tasks/' in path for path in opened)
    assert list_backups(settings)[0]['backup_id']==ident
    complete=json.loads((payload/'task-backups'/task.task_id/'complete.json').read_text())
    raw=payload/'task-backups'/task.task_id/complete['payload']
    assert (raw/'state/session.sqlite-wal').read_bytes()==b'WAL bytes'
    (raw/'state/session.sqlite-wal').write_bytes(b'changed')
    with pytest.raises(CloudError):verify_backup(settings,ident)


def test_failed_task_backup_never_publishes_complete_instance(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.backup import create_backup,list_backups
    import naiyue_test_agent.cloud_install.backup as module
    settings,_,_,_=fixture(tmp_path,monkeypatch);ident=str(uuid4())
    def fail(*a):raise CloudError('upgrade_backup_failed')
    monkeypatch.setattr(module,'backup_container',fail)
    with pytest.raises(CloudError):create_backup(settings,ident)
    assert list_backups(settings)[0]['phase']=='incomplete'
    assert not (settings.work_dir/'instance-backups'/ident/'complete.json').exists()
