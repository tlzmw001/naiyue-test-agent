"""Already selected project facts are checked, never used as rebind commands."""
import asyncio
from dataclasses import replace

import pytest

from naiyue_test_agent import application
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.execution_roots import Project
from naiyue_test_agent.storage.project_store import ProjectStore
from test_public_history_recovery import saved_project, assert_identity_unchanged


def test_existing_project_opens_without_rebinding_saved_missing_root(monkeypatch, tmp_path):
    root, loc, sid, model, settings, before = saved_project(monkeypatch, tmp_path)
    expected = Project(loc.workspace_id, root, loc.database_path)
    from naiyue_test_agent.storage.sessions import SessionRepo
    monkeypatch.setattr(SessionRepo, 'bind_workspace', lambda *_: pytest.fail('History startup must not rebind'))
    async def scenario():
        async with open_component_application('unused', database_path=loc.database_path,
                tools=[], system_prompt='', response_format=None, settings=settings, expected_project=expected) as app:
            assert any(m.content == 'saved public history' for m in (await app.host.history.session(sid))['messages'])
            await app.host.sessions.restore(sid)
            assert not (await app.host.sessions.status(sid))['can_run']
    asyncio.run(scenario())
    assert len(model.seen) == 1
    assert_identity_unchanged(loc, sid, before)


@pytest.mark.parametrize('change', ['missing', 'binding', 'path'])
def test_changed_project_after_preflight_fails_before_models_without_rebind(monkeypatch, tmp_path, change):
    root, loc, sid, model, settings, before = saved_project(monkeypatch, tmp_path)
    expected = Project(loc.workspace_id, root, loc.database_path)
    if change == 'missing':
        loc.database_path.rename(tmp_path / 'removed.sqlite')
    elif change == 'binding':
        with ProjectStore(loc.database_path) as store:
            with store.database.transaction() as connection:
                connection.execute('UPDATE naiyue_workspace SET root_path=?', (str(tmp_path / 'changed-root'),))
    else:
        expected = replace(expected, database_path=tmp_path / 'other.sqlite')
    called = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda *_: called.append(True))
    async def scenario():
        with pytest.raises((SessionPersistenceError, ValueError)):
            async with open_component_application('unused', database_path=loc.database_path,
                    tools=[], system_prompt='', response_format=None, settings=settings, expected_project=expected):
                pytest.fail('A changed recovery target must not start')
    asyncio.run(scenario())
    assert called == [] and not (tmp_path / 'other.sqlite').exists()
    if change == 'missing':
        assert not loc.database_path.exists()
    else:
        with ProjectStore(loc.database_path) as store:
            assert store.sessions.get(sid)['workspace_root'] == str(root)
            assert store.sessions.get_workspace_binding()['root_path'] == str(
                tmp_path / 'changed-root' if change == 'binding' else root)


def test_existing_open_requires_file_even_when_it_disappears_after_file_check(monkeypatch, tmp_path):
    from naiyue_test_agent.storage import database as module
    from naiyue_test_agent.storage.database import Database
    path = tmp_path / 'state.sqlite'
    with ProjectStore(path):
        pass
    original = module.check_store_file
    removed = []
    def check(value):
        result = original(value)
        if value == path and not removed:
            path.rename(tmp_path / 'original.sqlite')
            removed.append(True)
        return result
    monkeypatch.setattr(module, 'check_store_file', check)
    with pytest.raises(SessionPersistenceError, match='storage_unavailable'):
        Database(path, require_existing=True)
    assert removed == [True] and not path.exists()

