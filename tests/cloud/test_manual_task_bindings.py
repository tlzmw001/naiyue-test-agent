"""Local task identity survives restarts and protects parent deletion."""
from uuid import uuid4
import pytest
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.errors import SessionPersistenceError


def uid():return str(uuid4())


def populated(tmp_path):
    root=tmp_path/'source';root.mkdir();project=uid();sid=uid()
    store=ProjectStore(tmp_path/'sessions.sqlite');store.sessions.bind_workspace(project,root)
    store.sessions.create(sid,dict(thread_id=uid(),model_id='A',workspace_root=str(root),active_tool_names=[],permission_mode='default'))
    from langgraph.checkpoint.sqlite import SqliteSaver
    SqliteSaver(store.database.connection).setup()
    return store,project,sid


def test_reservation_is_idempotent_and_persistent_before_any_network(tmp_path):
    store,project,sid=populated(tmp_path)
    try:
        repo=store.cloud_tasks
        source=repo.add_source(sid,project,dict(input_id='a'*64,directory='/private/cache'))
        request=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
        target=dict(instance_id=uid(),project_name='code')
        first=repo.reserve(project,request,target)
        assert repo.reserve(project,request,target)==first
        assert first['binding']['task_id'] is None and first['operation']['phase']=='reserved'
        with pytest.raises(SessionPersistenceError,match='session_cloud_tasks_retained'):
            store.runs.delete(sid,())
        path=store.path
    finally:store.close()
    with ProjectStore(path) as reopened:
        assert reopened.cloud_tasks.operation(request['command_id'],sid)==first['operation']
        assert reopened.cloud_tasks.binding(first['binding']['binding_id'],sid)==first['binding']


def test_changed_command_cross_session_and_deleted_parent_are_rejected(tmp_path):
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    store,project,sid=populated(tmp_path)
    try:
        repo=store.cloud_tasks
        source=repo.add_source(sid,project,dict(input_id='a'*64,directory='/private/cache'))
        req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
        target=dict(instance_id=uid(),project_name='code');repo.reserve(project,req,target)
        with pytest.raises(CloudError,match='cloud_command_conflict'):repo.reserve(project,req|{'template_id':'other'},target)
        with pytest.raises(CloudError,match='cloud_binding_not_found'):repo.binding(repo.operation(req['command_id'],sid)['binding_id'],uid())
        with pytest.raises(CloudError,match='cloud_source_not_found'):repo.source(source['source_handle'],uid())
        other=uid();store.sessions.create(other,dict(thread_id=uid(),model_id='A',workspace_root=str(tmp_path/'source'),active_tool_names=[],permission_mode='default'))
        store.runs.delete(other,())
        with pytest.raises(SessionPersistenceError,match='session_not_found'):repo.add_source(other,project,{})
    finally:store.close()
