"""Application entries consume the selected root/settings and restore actual checkpoints."""

import asyncio
from pathlib import Path
import shutil

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent import application
from naiyue_test_agent.application import cli
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.workspaces import prepare_workspace
from test_application_run_events import ToolModel


def test_cli_directory_move_restores_same_session_history_and_new_working_root(tmp_path, monkeypatch, capsys):
    a, b, data = tmp_path / 'A', tmp_path / 'B', tmp_path / 'D盘数据'
    a.mkdir()
    elsewhere = tmp_path / 'terminal'
    elsewhere.mkdir()
    monkeypatch.chdir(a)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    options = AppSettings.model_validate({'storage': {'data_dir': data}})
    monkeypatch.setattr(cli, 'load_settings', lambda: options)
    model = ToolModel(responses=[AIMessage(content='first answer'), AIMessage(content='second answer')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    lines = iter(['first user request', ':quit'])
    monkeypatch.setattr('builtins.input', lambda *_: next(lines))
    assert cli.main(['--config', 'unused']) == 0
    loc = prepare_workspace(a, data_dir=data)
    with SessionStorage(loc.database_path) as storage:
        row = storage.list_sessions()[0]
    sid, thread, checkpoint = row['session_id'], row['thread_id'], row['checkpoint_id']
    monkeypatch.chdir(elsewhere)
    shutil.move(str(a), str(b))
    lines = iter(['second user request', ':quit'])
    monkeypatch.setattr('builtins.input', lambda *_: next(lines))
    assert cli.main(['--config', 'unused', '--workspace', str(b), '--resume', sid]) == 0
    assert not (elsewhere / '.naiyue').exists()
    assert 'second answer' in capsys.readouterr().out
    assert prepare_workspace(b, data_dir=data).database_path == loc.database_path
    with SessionStorage(loc.database_path) as storage:
        saved = storage.get(sid)
        assert saved['thread_id'] == thread and saved['workspace_root'] == str(b.resolve())
        assert saved['checkpoint_id'] != checkpoint and saved['state'] == 'ready'
        from langgraph.checkpoint.sqlite import SqliteSaver
        state = SqliteSaver(storage.checkpoint_connection).get_tuple({'configurable': {'thread_id': thread}})
        messages = state.checkpoint['channel_values']['messages']
        assert [m.content for m in messages if isinstance(m, HumanMessage)] == ['first user request', 'second user request']
        assert [m.content for m in messages if isinstance(m, AIMessage)] == ['first answer', 'second answer']


def test_binding_mismatch_and_lock_fail_before_model_loading(tmp_path, monkeypatch):
    root, other = tmp_path / 'A', tmp_path / 'B'
    root.mkdir()
    other.mkdir()
    loc = prepare_workspace(root, data_dir=tmp_path / 'data')
    called = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: called.append(True))
    async def scenario():
        args = dict(database_path=loc.database_path, tools=[], system_prompt='', response_format=None)
        with SessionStorage(loc.database_path) as storage:
            storage.bind_workspace(loc.workspace_id, loc.root)
            with pytest.raises(SessionPersistenceError, match='storage_in_use'):
                async with open_async_persistent_application('unused', **args):
                    pytest.fail('occupied storage must not open')
        with pytest.raises(SessionPersistenceError, match='workspace_database_mismatch'):
            async with open_async_persistent_application('unused', **args,
                    workspace_binding={'workspace_id': 'another-id', 'root_path': str(other)}):
                pytest.fail('another workspace must not enter')
    asyncio.run(scenario())
    assert called == []
    with SessionStorage(loc.database_path) as reopened:
        assert reopened.get_workspace_binding()['workspace_id'] == loc.workspace_id


def test_web_entry_passes_one_settings_snapshot_and_workspace_identity(tmp_path, monkeypatch):
    from naiyue_test_agent.web import __main__ as entry
    from naiyue_test_agent.web.app import create_web_app
    from test_async_application import StreamingToolModel
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'index.html').write_text('<html>fixture</html>')
    settings = AppSettings.model_validate({'storage': {'data_dir': tmp_path / 'data'}})
    calls, captured = [], []
    monkeypatch.setattr(entry, 'load_settings', lambda: (calls.append(True), settings)[1])
    monkeypatch.setattr(entry, 'create_web_app', lambda value: captured.append(value))
    monkeypatch.setattr(entry.uvicorn.Server, 'run', lambda self, **kw: setattr(self, 'started', True))
    monkeypatch.setenv('NAIYUE_WEB_PORT', '0')
    assert entry.main(['--frontend-dist', str(tmp_path)]) == 0
    assert len(calls) == 1
    configured = captured[0]
    assert configured.runtime_settings is settings
    assert configured.workspace_root == tmp_path.resolve()
    assert configured.database_path == tmp_path / 'data/workspaces' / configured.workspace_id / 'sessions.sqlite'
    assert 'runtime_settings' not in configured.model_dump()
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': StreamingToolModel(replies=[])}))
    # Lifespan must consume the snapshot, and bind before accepting HTTP operations.
    monkeypatch.setattr('naiyue_test_agent.web.app.load_settings', lambda: pytest.fail('settings loaded twice'))
    app = create_web_app(configured)
    async def scenario():
        async with app.router.lifespan_context(app):
            sid = await app.state.service.application.acreate_workspace_session('A', configured.workspace_root)
            rows = await app.state.service.sessions()
            assert [row['session_id'] for row in rows] == [sid]
    asyncio.run(scenario())
    with SessionStorage(configured.database_path) as storage:
        assert storage.get_workspace_binding()['workspace_id'] == configured.workspace_id


def test_new_workspace_flags_reject_incompatible_options(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for arguments in (['--no-session'], ['--resume'], ['--session-db', 'db']):
        with pytest.raises(SystemExit) as caught:
            cli.main(['--new-workspace', *arguments])
        assert caught.value.code == 2
    assert not (tmp_path / '.naiyue').exists()
