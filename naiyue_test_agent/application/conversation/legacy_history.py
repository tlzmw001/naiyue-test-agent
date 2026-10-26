"""Read an explicitly selected unavailable legacy project without granting execution."""
from pathlib import Path

from langchain_core.messages import BaseMessage
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.projects import LocalSource, ProjectError, RegisteredProject
from naiyue_test_agent.execution_roots import ExecutionRoot, execution_error
from naiyue_test_agent.storage.project_databases import read_legacy
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.resources import open_storage
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.storage.execution_storage import root_from_row
from naiyue_test_agent.application.sessions.session_recovery import _message_facts


async def legacy_project(path, *, root=None):
    path = Path(path).expanduser().absolute()
    identity, bound = await run_blocking(read_legacy, path)
    if root is not None and Path(root).expanduser().resolve() != bound:
        raise ProjectError('project_location_changed')
    error = await run_blocking(execution_error, ExecutionRoot(bound), identity)
    if error is None:
        raise ProjectError('project_identity_conflict')
    return RegisteredProject(identity, bound.name or str(bound), LocalSource(bound), path,
                             lifecycle='active'), error


async def legacy_sessions(path, *, root=None):
    project, error = await legacy_project(path, root=root)
    def read():
        with ProjectStore(project.database_path, require_existing=True,
                runtime_profile=project.project_id) as store:
            return store.sessions.list_sessions()
    return {'sessions': await run_blocking(read), 'root': str(project.source.path),
            'project_id': project.project_id, 'execution_root_error': error}


async def legacy_history(path, session_id):
    project, _ = await legacy_project(path)
    async with open_storage(project.database_path, registered_project=project) as resources:
        row = await run_blocking(resources.store.sessions.get, session_id)
        if row['project_id'] != project.project_id:
            raise SessionPersistenceError('session_not_found')
        saved = await resources.checkpointer.aget_tuple({'configurable': {'thread_id': row['thread_id']}})
        messages, checkpoint = [], None
        if saved is not None:
            scope = saved.config['configurable']
            if scope['thread_id'] != row['thread_id'] or scope.get('checkpoint_ns', ''):
                raise SessionPersistenceError('chat_history_identity_mismatch')
            messages = saved.checkpoint['channel_values'].get('messages', [])
            checkpoint = scope.get('checkpoint_id')
            if not isinstance(messages, list) or any(not isinstance(m, BaseMessage) for m in messages):
                raise SessionPersistenceError('chat_history_invalid_messages')
        missing, _ = _message_facts(messages)
        return {**{key: row[key] for key in ('session_id', 'model_id', 'thinking_level', 'workspace_root', 'state', 'permission_mode')},
            'messages': messages, 'checkpoint_id': checkpoint,
            'execution_root_error': ({'code': 'execution_root_unavailable', 'reason': 'not_registered'}
                if row['execution_binding'] == 'project_local' else execution_error(root_from_row(row), row['project_id'])),
            'recovery': {'kind': 'unavailable', 'reason': 'execution_root_unavailable',
                         'actions': [], 'unconfirmed_tool_call_ids': missing}}
