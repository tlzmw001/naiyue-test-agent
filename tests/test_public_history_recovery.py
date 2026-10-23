"""Public startup can read a selected old project without executing in its lost root."""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.cli import chat
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.web import __main__ as web_entry
from test_async_application import StreamingToolModel
from test_execution_root_contract import opened, project
from test_web_api import request


def saved_project(monkeypatch, folder, reason='missing'):
    root, location = project(folder)
    model = StreamingToolModel(replies=[AIMessage(content='saved public history')])
    async def save():
        async with opened(monkeypatch, folder, model, location=location) as app:
            sid = await app.sessions.create_workspace('A', root)
            await app.runs.start(sid, [('user', 'saved question')])
            return sid
    sid = asyncio.run(save())
    settings = AppSettings.model_validate({'storage': {'data_dir': folder / 'data'},
        'memory': {'auto_enabled': False}})
    with ProjectStore(location.database_path) as store:
        before = (store.sessions.get_workspace_binding(), store.sessions.get(sid))
    if reason in ('missing', 'not_directory'):
        root.rename(folder / 'removed')
        if reason == 'not_directory':
            root.write_text('not a directory')
    elif reason == 'wrong_project':
        (root / '.naiyue/workspace.json').write_text(json.dumps({'version': 1, 'workspace_id': str(uuid4())}))
    return root, location, sid, model, settings, before


def assert_identity_unchanged(location, sid, before):
    with ProjectStore(location.database_path) as store:
        assert store.sessions.get_workspace_binding() == before[0]
        row = store.sessions.get(sid)
        for key in ('project_id', 'workspace_root', 'execution_root_kind', 'thread_id', 'checkpoint_id', 'state'):
            assert row[key] == before[1][key]


@pytest.mark.parametrize('reason', ['missing', 'not_directory', 'wrong_project'])
@pytest.mark.parametrize('explicit_workspace', [False, True])
def test_public_cli_reads_history_without_rebinding_or_entering_input(
        monkeypatch, tmp_path, capsys, reason, explicit_workspace):
    root, location, sid, model, settings, before = saved_project(monkeypatch, tmp_path, reason)
    elsewhere = tmp_path / 'terminal'
    elsewhere.mkdir()
    sentinel = elsewhere / 'sentinel'
    sentinel.write_text('unchanged')
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(chat, 'load_settings', lambda: settings)
    monkeypatch.setattr('builtins.input', lambda *_: pytest.fail('Unavailable execution must stay in history display'))
    args = ['--config', 'unused', '--session-db', str(location.database_path), '--resume', sid]
    if explicit_workspace:
        args += ['--workspace', str(root)]
    assert chat.main(args) == 0
    output = capsys.readouterr().out
    assert 'saved public history' in output and 'saved question' in output
    assert '执行目录不可用' in output and reason in output
    assert len(model.seen) == 1
    assert sentinel.read_text() == 'unchanged' and not (elsewhere / '.naiyue').exists()
    assert_identity_unchanged(location, sid, before)


@pytest.mark.parametrize('reason', ['missing', 'not_directory', 'wrong_project'])
@pytest.mark.parametrize('explicit_workspace', [False, True])
def test_public_web_startup_lifespan_and_http_keep_history_but_deny_execution(
        monkeypatch, tmp_path, reason, explicit_workspace):
    root, location, sid, model, settings, before = saved_project(monkeypatch, tmp_path, reason)
    assets = tmp_path / 'frontend'
    assets.mkdir()
    (assets / 'index.html').write_text('<html>fixture</html>')
    elsewhere = tmp_path / 'terminal'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(web_entry, 'load_settings', lambda: settings)
    monkeypatch.setenv('NAIYUE_WEB_TOKEN', 'recovery-test-access')
    monkeypatch.setenv('NAIYUE_WEB_PORT', '0')
    observed = {}
    async def inspect(server):
        app = server.config.app
        origin = f'http://127.0.0.1:{server.config.port}'
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin,
                    headers={'Authorization': 'Bearer recovery-test-access', 'Origin': origin}) as client:
                observed['list'] = await client.get('/api/sessions')
                observed['prepare'] = await client.post(f'/api/sessions/{sid}/prepare')
                observed['view'] = await client.get(f'/api/sessions/{sid}')
                observed['denied'] = await client.post(f'/api/sessions/{sid}/runs', json=request('must not run'))
                observed['auth'] = await client.get('/api/sessions', headers={'Authorization': ''})
                observed['runs'] = len(app.state.delivery.runs)
    def run(server, **kwargs):
        asyncio.run(inspect(server))
        server.started = True
    monkeypatch.setattr(web_entry.uvicorn.Server, 'run', run)
    args = ['--config', 'unused', '--session-db', str(location.database_path), '--frontend-dist', str(assets)]
    if explicit_workspace:
        args += ['--workspace', str(root)]
    assert web_entry.main(args) == 0
    assert observed['list'].status_code == 200
    assert [row['session_id'] for row in observed['list'].json()] == [sid]
    assert observed['prepare'].status_code == observed['view'].status_code == 200
    view = observed['view'].json()
    assert view['project_id'] == location.workspace_id and view['workspace_root'] == str(root)
    assert view['can_run'] is False
    assert view['execution_root_error'] == {'code': 'execution_root_unavailable', 'reason': reason}
    assert any(message['content'] == 'saved public history' for message in view['messages'])
    assert observed['denied'].status_code == 409
    assert observed['denied'].json() == {'error': {'code': 'execution_root_unavailable', 'reason': reason}}
    assert observed['auth'].status_code == 401 and observed['runs'] == 0
    assert len(model.seen) == 1 and not (elsewhere / '.naiyue').exists()
    assert_identity_unchanged(location, sid, before)


@pytest.mark.parametrize('entry', [chat, web_entry])
@pytest.mark.parametrize('reason', ['missing', 'not_directory'])
def test_unavailable_root_without_database_explains_how_to_select_history(monkeypatch, tmp_path, capsys, entry, reason):
    monkeypatch.setattr(entry, 'load_settings', lambda: AppSettings())
    root = tmp_path / 'missing'
    if reason == 'not_directory':
        root.write_text('not a directory')
    args = ['--workspace', str(root)]
    if entry is chat:
        args += ['--resume', 'saved-id']
    else:
        (tmp_path / 'index.html').write_text('<html>fixture</html>')
        args += ['--frontend-dist', str(tmp_path)]
    assert entry.main(args) == 1
    assert '--session-db' in capsys.readouterr().out
    assert not (tmp_path / '.naiyue').exists()
    if reason == 'not_directory':
        assert root.read_text() == 'not a directory'
    else:
        assert not root.exists()


@pytest.mark.parametrize('entry', [chat, web_entry])
def test_explicit_missing_history_database_never_initializes_workspace(monkeypatch, tmp_path, capsys, entry):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entry, 'load_settings', lambda: AppSettings())
    missing = tmp_path / 'not-created.sqlite'
    args = ['--session-db', str(missing), '--config', 'unused']
    if entry is chat:
        args += ['--resume', 'saved-id']
    else:
        (tmp_path / 'index.html').write_text('<html>fixture</html>')
        args += ['--frontend-dist', str(tmp_path)]
    assert entry.main(args) == 1
    assert not missing.exists() and not (tmp_path / '.naiyue').exists()


def test_cli_ready_valid_directory_still_accepts_new_message(monkeypatch, tmp_path, capsys):
    root, location, sid, model, settings, before = saved_project(monkeypatch, tmp_path, reason=None)
    model.replies.append(AIMessage(content='continued answer'))
    monkeypatch.setattr(chat, 'load_settings', lambda: settings)
    lines = iter(['next question', ':quit'])
    monkeypatch.setattr('builtins.input', lambda *_: next(lines))
    assert chat.main(['--config', 'unused', '--session-db', str(location.database_path), '--resume', sid]) == 0
    assert 'continued answer' in capsys.readouterr().out and len(model.seen) == 2

