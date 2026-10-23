"""Session modes persist; remembered approvals remain process-local."""

from native_application_helpers import async_test, build_application

import sqlite3

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.application.errors import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.settings import AppSettings
from test_dynamic_tools import DynamicModel, definition
from test_permission_pipeline import call


def persistent(monkeypatch, storage, replies):
    model = DynamicModel(responses=replies)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    return open_async_persistent_application('synthetic', database_path=storage, tools=[create_session_read_tool()],
        settings=AppSettings(), system_prompt='', response_format=None)


@async_test
async def test_mode_persists_but_remembered_target_does_not(monkeypatch, tmp_path):
    root, target = tmp_path / 'workspace', tmp_path / 'outside.txt'
    root.mkdir(); target.write_text('data')
    database = tmp_path / 'sessions.sqlite'
    async with persistent(monkeypatch, database, [AIMessage(content='', tool_calls=[call(target)]), AIMessage(content='done')]) as app:
        storage = app._storage
        sid = await app.acreate_workspace_session('A', root)
        await app.arun(sid, [('user', 'read')])
        await app.aresume_tool_approval(sid, ['approve'], remember_approved=['c1'])
        assert app._sessions.resolve_permission_context(sid)['session_allow']
        await app.aset_permission_mode(sid, 'NoAsk')
        assert storage.sessions.get(sid)['permission_mode'] == 'NoAsk'
    async with persistent(monkeypatch, database, []) as app:
        storage = app._storage
        await app.arestore_session(sid)
        assert app.get_permission_mode(sid) == 'NoAsk'
        assert app._sessions.resolve_permission_context(sid)['session_allow'] == []
        assert app._sessions.require(sid).describe_session()['permission_mode'] == 'NoAsk'


@async_test
async def test_mode_save_failure_does_not_change_published_mode(monkeypatch, tmp_path):
    async with persistent(monkeypatch, tmp_path / 'sessions.sqlite', []) as app:
        storage = app._storage
        sid = await app.acreate_session('A')
        def fail(*args):
            raise SessionPersistenceError('synthetic_save_failed')
        monkeypatch.setattr(storage.sessions, 'select_permission_mode', fail)
        with pytest.raises(SessionPersistenceError):
            await app.aset_permission_mode(sid, 'NoAsk')
        assert app.get_permission_mode(sid) == storage.sessions.get(sid)['permission_mode'] == 'Default'
        with pytest.raises(ValueError):
            await app.aset_permission_mode(sid, 'Auto')


def test_v2_database_is_rejected_without_migration(tmp_path):
    path = tmp_path / 'v2.sqlite'
    connection = sqlite3.connect(path)
    path.chmod(0o600)
    connection.executescript('''
        CREATE TABLE naiyue_store_info(version INTEGER NOT NULL);
        INSERT INTO naiyue_store_info VALUES(2);
        CREATE TABLE naiyue_sessions(session_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE,
            model_id TEXT NOT NULL, workspace_root TEXT, state TEXT NOT NULL, run_id TEXT,
            checkpoint_id TEXT, checkpoint_ns TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, last_run_status TEXT, active_tool_names_json TEXT);
        INSERT INTO naiyue_sessions VALUES('old','thread','A',NULL,'ready',NULL,NULL,'','t','t',NULL,'[]');
    ''')
    connection.close()
    before = path.read_bytes()
    for _ in range(2):
        with pytest.raises(SessionPersistenceError, match='unsupported_store_schema'):
            ProjectStore(path)
        assert path.read_bytes() == before


@async_test
async def test_unknown_rule_stops_before_loading_model_catalog(monkeypatch):
    reached = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: reached.append(True))
    settings = AppSettings.model_validate({'permissions': {'deny': [{'tool': 'missing'}]}})
    with pytest.raises(ValueError, match='unknown tool'):
        await build_application('synthetic', tools=[create_session_read_tool()],
            settings=settings, system_prompt='', response_format=None)
    assert reached == []


@async_test
async def test_dynamic_registration_validates_wildcard_rule_before_publishing(monkeypatch):
    from langchain.tools import tool
    from naiyue_test_agent.tools import ToolSpec
    from naiyue_test_agent.tools.permissions import ToolPermission
    model = DynamicModel(responses=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    settings = AppSettings.model_validate({'permissions': {'allow': [{'tool': '*', 'args': {'value': 'ok'}}]}})
    app = await build_application('synthetic', tools=[definition()], settings=settings,
        system_prompt='', response_format=None)
    @tool
    def incompatible(count: int) -> str:
        """Has no public value argument required by the wildcard rule."""
        return str(count)
    with pytest.raises(ValueError, match='unknown public argument'):
        await app.aregister_tool(ToolSpec(incompatible, incompatible.args_schema, permissions=ToolPermission(default='allow')))
    assert [s.tool.name for s in app.get_registered_tools()] == ['probe']
