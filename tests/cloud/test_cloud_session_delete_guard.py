"""Deletion/whole-store restore cannot erase a live external delegation."""
import asyncio
from contextlib import closing
import pytest
from test_manual_task_bindings import populated,uid
from naiyue_test_agent.storage.project_store import ProjectStore


def reserve(store,project,sid):
    repo=store.cloud_tasks
    source=repo.add_source(sid,project,dict(input_id='a'*64,directory='/private/cache'))
    request=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
    return repo.reserve(project,request,dict(instance_id=uid(),project_name='code'))


def test_closed_project_deletion_checks_the_persistent_binding(tmp_path):
    from test_project_registry import service
    from naiyue_test_agent.contracts.projects import ProjectError
    root=tmp_path/'source';root.mkdir()
    with service(tmp_path) as projects:
        project=projects.add_local(root,trusted=True,operation_id='add')
        with ProjectStore(project.database_path) as store:
            sid=uid();store.sessions.create(sid,dict(thread_id=uid(),model_id='A',workspace_root=str(root),active_tool_names=[],permission_mode='Default'))
            reserve(store,project.project_id,sid)
        with pytest.raises(ProjectError,match='project_cloud_tasks_retained'):
            projects.remove(project.project_id,operation_id='remove')
        assert projects.get(project.project_id).lifecycle=='active'
        assert project.database_path.exists()


def test_restoring_an_older_snapshot_cannot_forget_retained_delegations(tmp_path):
    from test_storage_restore import saved_store
    from naiyue_test_agent.storage.restore import restore_backup
    location,saved=saved_store(tmp_path,version=20)
    with ProjectStore(location.database_path) as store:reserve(store,location.workspace_id,'S')
    before=location.database_path.read_bytes();confirmed=[]
    with pytest.raises(ValueError,match='cloud_tasks_retained'):
        restore_backup(location,saved['backup_id'],confirm=lambda p:confirmed.append(p) or saved['backup_id'])
    assert not confirmed and location.database_path.read_bytes()==before
    with ProjectStore(location.database_path) as store:assert len(store.cloud_tasks.list('S'))==1


def test_deleted_parent_cannot_admit_a_task(tmp_path):
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    store,project,sid=populated(tmp_path)
    try:
        source=store.cloud_tasks.add_source(sid,project,dict(input_id='a'*64,directory='/private/cache'))
        store.runs.delete(sid,())
        req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
        with pytest.raises(SessionPersistenceError,match='session_not_found'):
            store.cloud_tasks.reserve(project,req,dict(instance_id=uid(),project_name='code'))
        assert store.database.connection.execute('SELECT COUNT(*) FROM naiyue_cloud_bindings').fetchone()[0]==0
    finally:store.close()


def test_damaged_cloud_schema_is_not_proof_that_restoring_is_safe(tmp_path):
    from test_storage_restore import saved_store
    from naiyue_test_agent.storage.restore import restore_backup
    location,saved=saved_store(tmp_path,version=20)
    with ProjectStore(location.database_path) as store:
        reserve(store,location.workspace_id,'S')
        (store.path.parent/'cloud-input-cache').mkdir()
        store.database.connection.execute('DROP TABLE naiyue_cloud_bindings')
    confirmed=[]
    with pytest.raises(ValueError,match='cloud_tasks_reconciliation_required'):
        restore_backup(location,saved['backup_id'],confirm=lambda p:confirmed.append(p) or saved['backup_id'])
    assert not confirmed
