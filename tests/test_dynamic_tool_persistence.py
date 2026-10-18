"""Tool choices are necessary session metadata, including explicit empty lists."""

from native_application_helpers import async_test

import json
import sqlite3

import pytest

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from test_dynamic_tools import DynamicModel, definition


def build(monkeypatch, storage, names=None, specs=None):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': DynamicModel(responses=[])}))
    return open_async_persistent_application('synthetic', database_path=storage, tools=specs or [definition()],
        initial_active_tools=names, system_prompt='', response_format=None)


@async_test
async def test_empty_selection_survives_reopen(monkeypatch, tmp_path):
    path = tmp_path / 'sessions.sqlite'
    async with build(monkeypatch, path) as app:
        storage = app._storage
        sid = await app.acreate_session('A')
        await app.aset_active_tools(sid, [])
        assert json.loads(storage.get(sid)['active_tool_names_json']) == []
    async with build(monkeypatch, path) as app:
        storage = app._storage
        await app.arestore_session(sid)
        assert app.get_active_tools(sid) == ()
        assert app._sessions.require(sid).describe_session()['active_tools'] == []


@async_test
async def test_save_failure_keeps_old_selection(monkeypatch, tmp_path):
    async with build(monkeypatch, tmp_path / 'sessions.sqlite') as app:
        storage = app._storage
        sid = await app.acreate_session('A')
        def fail(*args):
            raise SessionPersistenceError('synthetic_save_failed')
        monkeypatch.setattr(storage, 'select_active_tools', fail)
        with pytest.raises(SessionPersistenceError):
            await app.aset_active_tools(sid, [])
        assert app.get_active_tools(sid) == ('probe',)
        assert json.loads(storage.get(sid)['active_tool_names_json']) == ['probe']


@pytest.mark.parametrize('saved', ['broken-json', '["missing"]', '"probe"', '["probe","probe"]'])
@async_test
async def test_invalid_saved_selection_fails_without_publishing(monkeypatch, tmp_path, saved):
    path = tmp_path / 'sessions.sqlite'
    async with build(monkeypatch, path) as app:
        storage = app._storage
        sid = await app.acreate_session('A')
        storage.connection.execute('UPDATE naiyue_sessions SET active_tool_names_json=? WHERE session_id=?', (saved, sid))
        storage.connection.commit()
    async with build(monkeypatch, path) as app:
        storage = app._storage
        with pytest.raises(SessionPersistenceError, match='invalid_active_tools'):
            await app.arestore_session(sid)
        assert not app._sessions.has_session(sid)


def test_v1_database_is_rejected_without_migration(monkeypatch, tmp_path):
    path = tmp_path / 'sessions.sqlite'
    # Build a real prior-version database, never downgrade a current schema.
    connection = sqlite3.connect(path)
    path.chmod(0o600)
    connection.executescript('''
        CREATE TABLE naiyue_store_info(version INTEGER NOT NULL);
        INSERT INTO naiyue_store_info VALUES(1);
        CREATE TABLE naiyue_sessions(session_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE,
            model_id TEXT NOT NULL, workspace_root TEXT, state TEXT NOT NULL, run_id TEXT,
            checkpoint_id TEXT, checkpoint_ns TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, last_run_status TEXT);
        INSERT INTO naiyue_sessions VALUES('old','thread','A',NULL,'ready',NULL,NULL,'','t','t',NULL);
    ''')
    connection.close()
    before = path.read_bytes()
    with pytest.raises(SessionPersistenceError, match='unsupported_store_schema'):
        SessionStorage(path)
    assert path.read_bytes() == before
