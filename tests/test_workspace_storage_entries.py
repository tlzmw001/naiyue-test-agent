"""Application entries consume the selected root/settings and restore actual checkpoints."""

import asyncio
from pathlib import Path
import shutil

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent import application
from naiyue_test_agent.cli import chat as cli
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.workspaces import prepare_workspace
from test_application_run_events import ToolModel


def test_cli_project_relocation_moves_existing_and_new_sessions_to_explicit_position(tmp_path, monkeypatch, capsys):
    from uuid import uuid4
    from naiyue_test_agent.application.projects.service import ProjectService
    a, b, data = tmp_path / 'A', tmp_path / 'B', tmp_path / 'application-data'
    a.mkdir()
    monkeypatch.chdir(a)
    options = AppSettings.model_validate({'storage': {'data_dir': data}})
    monkeypatch.setattr(cli, 'load_settings', lambda: options)
    model = ToolModel(responses=[AIMessage(content='first answer'), AIMessage(content='second answer'), AIMessage(content='third answer')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    lines = iter(['first user request', ':quit'])
    monkeypatch.setattr('builtins.input', lambda *_: next(lines))
    assert cli.main(['--config', 'unused']) == 0
    registry = ProjectService(data, options.projects)
    try:
        registered, = registry.list()
    finally:
        registry.close()
    assert not (a / '.naiyue/workspace.json').exists()
    with ProjectStore(registered.database_path) as storage:
        row, = storage.sessions.list_sessions()
    sid, thread, checkpoint = row['session_id'], row['thread_id'], row['checkpoint_id']
    shutil.move(str(a), str(b))
    monkeypatch.chdir(tmp_path)
    registry = ProjectService(data, options.projects)
    try:
        registry.relocate(registered.project_id, expected_root=a, root=b,
            trusted=True, operation_id=str(uuid4()))
    finally:
        registry.close()
    capsys.readouterr()
    lines = iter(['continued user request', ':quit'])
    monkeypatch.setattr('builtins.input', lambda *_: next(lines))
    assert cli.main(['--config', 'unused', '--project', registered.project_id, '--resume', sid]) == 0
    output = capsys.readouterr().out
    # Ready resume enters normal chat instead of the old read-only renderer;
    # the complete original + continued history is checked in SQLite below.
    assert 'second answer' in output
    assert '执行目录不可用' not in output and model.i == 2
    lines = iter(['new session request', ':quit'])
    monkeypatch.setattr('builtins.input', lambda *_: next(lines))
    assert cli.main(['--config', 'unused', '--project', registered.project_id]) == 0
    assert 'third answer' in capsys.readouterr().out
    with ProjectStore(registered.database_path) as storage:
        saved = storage.sessions.get(sid)
        assert (saved['thread_id'], saved['workspace_root'], saved['last_execution_path'], saved['state']) == (
            thread, str(b.resolve()), str(b.resolve()), 'ready')
        assert saved['checkpoint_id'] != checkpoint and saved['execution_binding'] == 'project_local'
        rows = storage.sessions.list_sessions()
        fresh = next(item for item in rows if item['session_id'] != sid)
        assert fresh['project_id'] == registered.project_id and fresh['workspace_root'] == str(b.resolve())
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        async def read_checkpoints():
            async with AsyncSqliteSaver.from_conn_string(str(storage.path)) as saver:
                return [await saver.aget_tuple({'configurable': {'thread_id': item}})
                        for item in (thread, fresh['thread_id'])]
        old, new = asyncio.run(read_checkpoints())
        assert [m.content for m in old.checkpoint['channel_values']['messages']] == [
            'first user request', 'first answer', 'continued user request', 'second answer']
        assert [m.content for m in new.checkpoint['channel_values']['messages']] == ['new session request', 'third answer']


def test_binding_mismatch_and_lock_fail_before_model_loading(tmp_path, monkeypatch):
    root, other = tmp_path / 'A', tmp_path / 'B'
    root.mkdir()
    other.mkdir()
    loc = prepare_workspace(root, data_dir=tmp_path / 'data')
    called = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: called.append(True))
    async def scenario():
        args = dict(database_path=loc.database_path, tools=[], system_prompt='', response_format=None)
        with ProjectStore(loc.database_path) as storage:
            storage.sessions.bind_workspace(loc.workspace_id, loc.root)
            with pytest.raises(SessionPersistenceError, match='storage_in_use'):
                async with open_component_application('unused', **args):
                    pytest.fail('occupied storage must not open')
        with pytest.raises(SessionPersistenceError, match='workspace_database_mismatch'):
            async with open_component_application('unused', **args,
                    workspace_binding={'workspace_id': 'another-id', 'root_path': str(other)}):
                pytest.fail('another workspace must not enter')
    asyncio.run(scenario())
    assert called == []
    with ProjectStore(loc.database_path) as reopened:
        assert reopened.sessions.get_workspace_binding()['workspace_id'] == loc.workspace_id


def test_web_entry_passes_explicit_settings_path_and_project_identity(tmp_path, monkeypatch):
    from naiyue_test_agent.web import __main__ as entry
    from web_service_helpers import isolated_web_launch
    from test_async_application import StreamingToolModel
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'index.html').write_text('<html>fixture</html>')
    settings = AppSettings.model_validate({'storage': {'data_dir': tmp_path / 'data'}})
    calls, captured = [], []
    monkeypatch.setattr(entry, 'load_settings', lambda path: (calls.append(path), settings)[1])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': StreamingToolModel(replies=[])}))
    async def inspect(client, server):
        rows = await client.request('GET', '/api/v1/projects')
        project, = rows['items']
        pid = project['project_id']
        base = f'/api/v1/scopes/{pid}'
        sid = (await client.request('POST', base + '/sessions', json={'model_id': 'A'}))['session_id']
        row, = await client.request('GET', base + '/sessions')
        assert row['session_id'] == sid
        view = await client.request('GET', base + f'/sessions/{sid}')
        assert view['project_id'] == pid and view['workspace_root'] == str(tmp_path.resolve())
        captured.append(project)
    path = tmp_path / 'app-settings.toml'
    with isolated_web_launch(monkeypatch, observe=inspect):
        assert entry.main(['--frontend-dist', str(tmp_path), '--settings', str(path), '--trust-project']) == 0
    assert calls == [path, path]  # launcher and separate service each load the selected path
    assert len(captured) == 1
    assert not (tmp_path / '.naiyue/workspace.json').exists()
    with ProjectStore(captured[0]['database_path']) as storage:
        assert storage.sessions.get_workspace_binding()['workspace_id'] == captured[0]['project_id']


def test_new_workspace_flags_reject_incompatible_options(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for arguments in (['--no-session'], ['--resume'], ['--session-db', 'db']):
        with pytest.raises(SystemExit) as caught:
            cli.main(['--new-workspace', *arguments])
        assert caught.value.code == 2
    assert not (tmp_path / '.naiyue').exists()
