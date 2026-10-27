"""Selected history never grants execution or takes over another project binding."""
import asyncio
import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.cli import chat
from naiyue_test_agent.execution_roots import Project
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.workspaces import locate_stored_project, prepare_workspace
from test_async_application import StreamingToolModel, call
from test_public_history_recovery import saved_project, assert_identity_unchanged


@pytest.mark.parametrize('binding', ['unbound', 'invalid_uuid', 'relative_root'])
def test_stored_project_requires_valid_binding_and_does_not_create_identity(tmp_path, binding):
    path = tmp_path / 'state.sqlite'
    with ProjectStore(path) as store:
        if binding != 'unbound':
            identity = str(uuid4()) if binding == 'relative_root' else 'invalid'
            with store.database.transaction() as connection:
                connection.execute('INSERT INTO naiyue_workspace VALUES(1,?,?)',
                    (identity, 'relative' if binding == 'relative_root' else str(tmp_path)))
    with pytest.raises(ValueError, match='invalid_workspace_binding'):
        locate_stored_project(path)
    assert not (tmp_path / '.naiyue').exists()


def test_foreign_explicit_workspace_cannot_take_over_selected_history(monkeypatch, tmp_path, capsys):
    root, loc, sid, model, settings, before = saved_project(monkeypatch, tmp_path)
    other = tmp_path / 'other-project'
    other.mkdir()
    prepare_workspace(other, data_dir=tmp_path / 'data')
    marker = (other / '.naiyue/workspace.json').read_bytes()
    called = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda *_: called.append(True))
    monkeypatch.setattr(chat, 'load_settings', lambda: settings)
    assert chat.main(['--config', 'unused', '--session-db', str(loc.database_path),
                     '--workspace', str(other), '--resume', sid]) == 1
    assert 'saved public history' not in capsys.readouterr().out and called == []
    assert (other / '.naiyue/workspace.json').read_bytes() == marker
    assert_identity_unchanged(loc, sid, before)


def test_foreign_session_project_is_rejected_before_history_is_displayed(monkeypatch, tmp_path, capsys):
    root, loc, sid, model, settings, before = saved_project(monkeypatch, tmp_path)
    with ProjectStore(loc.database_path) as store:
        with store.database.transaction() as connection:
            connection.execute('UPDATE naiyue_sessions SET project_id=? WHERE session_id=?', (str(uuid4()), sid))
    called = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda *_: called.append(True))
    monkeypatch.setattr(chat, 'load_settings', lambda: settings)
    assert chat.main(['--config', 'unused', '--session-db', str(loc.database_path), '--resume', sid]) == 1
    assert called == [] and 'saved public history' not in capsys.readouterr().out


@pytest.mark.parametrize('mode', ['approval', 'interrupted'])
def test_public_cli_lost_root_does_not_resume_approval_or_interrupted_run(monkeypatch, tmp_path, capsys, mode):
    root = tmp_path / 'execution'
    root.mkdir()
    target = tmp_path / 'external.txt'
    target.write_text('must never be read')
    loc = prepare_workspace(root, data_dir=tmp_path / 'data', database_path=tmp_path / 'state.sqlite')
    replies = ([call('read', {'path': str(target)})] if mode == 'approval'
               else [AIMessage(content='confirmed answer'), RuntimeError('model failed')])
    model = StreamingToolModel(replies=replies)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    settings = AppSettings.model_validate({'storage': {'data_dir': tmp_path / 'data'}, 'memory': {'auto_enabled': False}})
    async def save():
        async with open_component_application('unused', database_path=loc.database_path,
                tools=[create_session_read_tool()], system_prompt='', response_format=None, settings=settings,
                workspace_binding={'workspace_id': loc.workspace_id, 'root_path': root}) as app:
            sid = await app.host.sessions.create_workspace('A', root)
            result = await app.host.runs.start(sid, [('user', 'saved original request')])
            if mode == 'approval':
                assert result['approval_requests']
            else:
                with pytest.raises(RuntimeError, match='model failed'):
                    await app.host.runs.start(sid, [('user', 'failed second request')])
            return sid
    sid = asyncio.run(save())
    with ProjectStore(loc.database_path) as store:
        before = (store.sessions.get_workspace_binding(), store.sessions.get(sid))
    seen = len(model.seen)
    root.rename(tmp_path / 'removed')
    monkeypatch.setattr(chat, 'load_settings', lambda: settings)
    monkeypatch.setattr('builtins.input', lambda *_: pytest.fail('No automatic recovery dialogue in a lost root'))
    assert chat.main(['--config', 'unused', '--session-db', str(loc.database_path), '--resume', sid]) == 0
    output = capsys.readouterr().out
    assert 'saved original request' in output and 'missing' in output
    assert len(model.seen) == seen and target.read_text() == 'must never be read'
    assert_identity_unchanged(loc, sid, before)


def test_existing_project_and_binding_intents_cannot_be_combined(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda *_: called.append(True))
    path = tmp_path / 'must-not-create.sqlite'
    async def scenario():
        with pytest.raises(ValueError, match='mutually exclusive'):
            async with open_component_application('unused', database_path=path,
                    tools=[], system_prompt='', response_format=None,
                    workspace_binding={'workspace_id': 'other', 'root_path': tmp_path},
                    expected_project=Project(str(uuid4()), tmp_path, path)):
                pytest.fail('Conflicting startup intent must not open storage')
    asyncio.run(scenario())
    assert not path.exists() and called == []


def test_async_saver_existing_open_failure_releases_store_and_keeps_original_error(monkeypatch, tmp_path):
    from naiyue_test_agent.storage import resources
    root, loc, sid, model, settings, before = saved_project(monkeypatch, tmp_path)
    expected = Project(loc.workspace_id, root, loc.database_path)
    original = OSError('private async open failure')
    observed = []
    def fail(target, **kwargs):
        observed.append((target, kwargs))
        raise original
    monkeypatch.setattr(resources.aiosqlite, 'connect', fail)
    async def scenario():
        with pytest.raises(OSError) as caught:
            async with resources.open_storage(loc.database_path, expected_project=expected):
                pytest.fail('Failed Saver open must not yield storage')
        assert caught.value is original
    asyncio.run(scenario())
    assert observed == [(loc.database_path.as_uri() + '?mode=rw', {'timeout': 5.0, 'uri': True})]
    assert_identity_unchanged(loc, sid, before)
