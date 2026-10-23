"""Project data survives execution locations; personal memory crosses projects."""

from naiyue_test_agent.storage.queries import write_one

from contextlib import contextmanager

from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.tools.history import HistoryInput
from naiyue_test_agent.tools.history_settings import HistorySettings
from naiyue_test_agent.tools.memory import MemoryInput
from naiyue_test_agent.workspaces import prepare_workspace
from test_history_tool_storage import save
from test_memory_lifecycle import source, proposal
from langchain_core.messages import HumanMessage


@contextmanager
def project(folder, name):
    root = folder / name
    root.mkdir()
    location = prepare_workspace(root, data_dir=folder / 'data')
    with ProjectStore(location.database_path) as store:
        store.sessions.bind_workspace(location.workspace_id, root)
        for sid in ('caller', 'target'):
            store.sessions.create(sid, dict(thread_id=sid, model_id='A', workspace_root=root,
                active_tool_names=['memory', 'history'], permission_mode='Default'))
        access = MemoryAccess(store, MemorySettings(), folder / 'data')
        try:
            yield root, location.workspace_id, store, access
        finally:
            access.close()


def test_history_uses_project_for_targets_with_other_or_missing_execution_paths(tmp_path):
    with project(tmp_path, 'project') as (root, identity, store, _):
        save(store, 'target', [HumanMessage(id='h', content='saved in another execution root')])
        # Persisted data fixture for the future worktree producer; it does not enable execution.
        with store.database.transaction(write=True) as connection:
            write_one(connection, 'UPDATE naiyue_sessions SET workspace_root=?,execution_root_kind=? WHERE session_id=?',
                         (str(tmp_path / 'gone-worktree'), 'worktree', 'target'))
        assert {row['session_id'] for row in store.sessions.list_workspace_sessions(root)} == {'caller', 'target'}
        directory = store.conversation.read_history_tool('caller', identity,
            HistoryInput(action='list_sessions').model_dump(), HistorySettings())
        assert [row['session_id'] for row in directory['items']] == ['target']
        detail = store.conversation.read_history_tool('caller', identity,
            HistoryInput(action='read', session_id='target').model_dump(), HistorySettings())
        assert detail['items'][0]['content'] == 'saved in another execution root'


def test_saved_memory_sources_remain_valid_after_execution_directory_disappears(tmp_path):
    with project(tmp_path, 'project') as (root, identity, _, access):
        reference = source(access, 'caller', text='所有项目回答都使用中文')
        root.rename(tmp_path / 'removed')
        request = MemoryInput(action='record', scope='personal',
            proposal=proposal(kind='user', body='回答使用中文'), sources=[reference]).model_dump(mode='json')
        saved = access.query('caller', identity, request, now=2000)['item']
        request = MemoryInput(action='read', scope='personal', memory_id=saved['memory_id']).model_dump(mode='json')
        item = access.query('caller', identity, request, now=2000)['items'][0]
        assert item['scope'] == 'personal' and item['sources'][0]['available']
        assert not root.exists()


def test_personal_memory_is_shared_but_foreign_source_text_is_not(tmp_path):
    with project(tmp_path, 'one') as (_, identity, _, access):
        reference = source(access, 'caller', text='所有项目回答都使用中文')
        request = MemoryInput(action='record', scope='personal',
            proposal=proposal(kind='user', body='回答使用中文'), sources=[reference]).model_dump(mode='json')
        saved = access.query('caller', identity, request, now=2000)['item']
    with project(tmp_path, 'two') as (_, identity, _, access):
        request = MemoryInput(action='read', scope='personal', memory_id=saved['memory_id']).model_dump(mode='json')
        item = access.query('caller', identity, request, now=2000)['items'][0]
        assert item['body'] == '回答使用中文'
        assert item['sources'][0]['reason'] == 'other_workspace'
        assert not item['sources'][0]['available'] and 'quote' not in item['sources'][0]
