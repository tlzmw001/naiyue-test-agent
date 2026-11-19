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


def test_input_retirement_blocks_backup_until_original_delete_is_reconciled(tmp_path,monkeypatch):
    import asyncio
    import errno

    from naiyue_test_agent.application.cloud import input_reclamation
    from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
    from naiyue_test_agent.cloud_install.backup import create_backup,list_backups,verify_backup
    from test_instance_restore import prepare_one
    from test_task_delete import Backend,request

    settings,cloud,task,_=setup(tmp_path)
    monkeypatch.setattr('naiyue_test_agent.cloud_install.offline.ensure_no_writers',lambda *a:None)

    async def fail_cleanup(runtime):
        runtime.releases.maintenance(dict(command_id=str(uuid4()),action='leave'))
        async with open_cloud_backend(runtime,backend=Backend()):
            def deny(*args):
                raise PermissionError(errno.EACCES,'controlled input failure')
            with monkeypatch.context() as patch:
                patch.setattr(input_reclamation,'remove',deny)
                failed=await runtime.deletions.command(task.task_id,request(runtime,task.task_id))
            assert failed.status=='succeeded'
            assert failed.result['input_cleanup']['state']=='failed'
            return failed.command_id

    with open_cloud_runtime(cloud) as runtime:
        command_id=asyncio.run(fail_cleanup(runtime))
        status=runtime.releases.maintenance(dict(command_id=str(uuid4()),action='enter'))
    backup_id=str(uuid4())
    with pytest.raises(CloudError,match='offline_not_quiet'):
        create_backup(settings,backup_id)
    assert not (settings.work_dir/'instance-backups'/backup_id/'complete.json').exists()
    assert status['maintenance']=='draining'
    assert {'kind':'input_cleanup','id':command_id} in status['blockers']

    old_backup_id=str(uuid4())
    with monkeypatch.context() as patch:
        # Recreate a snapshot that the earlier backup gate let through.
        patch.setattr('naiyue_test_agent.storage.cloud.operations.blockers',lambda db:[])
        assert create_backup(settings,old_backup_id)['phase']=='completed'
    with pytest.raises(CloudError,match='restore_input_retirement_pending'):
        verify_backup(settings,old_backup_id)
    assert next(row for row in list_backups(settings) if row['backup_id']==old_backup_id)['phase']=='invalid'

    async def finish_cleanup(runtime):
        async with open_cloud_backend(runtime,backend=Backend()):
            final=await runtime.deletions.reconcile(command_id)
            assert final.result['input_cleanup']['state']=='completed'
            assert runtime.releases.maintenance_status()['maintenance']=='ready'

    with open_cloud_runtime(cloud) as runtime:
        asyncio.run(finish_cleanup(runtime))
    assert create_backup(settings,backup_id)['phase']=='completed'
    assert verify_backup(settings,backup_id)[0]['backup_id']==backup_id
    assert prepare_one(settings,backup_id)[1]['phase']=='prepared'
