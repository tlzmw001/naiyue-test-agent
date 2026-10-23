"""Persistent sessions keep framework state and application recovery boundaries."""

from native_application_helpers import async_test

import sqlite3

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.application.errors import SessionPersistenceError
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog
from test_application_run_events import ToolModel


def build(monkeypatch, storage, model=None, tools=None):
    model = model or ToolModel(responses=[AIMessage(content='answer')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    return open_async_persistent_application('unused', database_path=storage, tools=tools or [],
                                        system_prompt='', response_format=None)


@async_test
async def test_close_reopen_preserves_ids_tool_history_and_has_no_old_tool_execution(monkeypatch, tmp_path):
    db = tmp_path / 'sessions.sqlite'
    (tmp_path / 'note.txt').write_text('SYNTHETIC_TOOL_RESULT')
    async with build(monkeypatch, db, ToolModel(responses=[
            AIMessage(content='before', tool_calls=[{'name': 'read', 'args': {'path': 'note.txt'}, 'id': 't'}]),
            AIMessage(content='after'),
        ]), [create_session_read_tool()]) as first:
        storage = first._storage
        session = await first.acreate_workspace_session('A', tmp_path)
        result = await first.arun(session, [HumanMessage(content='original request', id='h')])
        row = storage.sessions.get(session)
        assert row['state'] == 'ready' and row['checkpoint_id']
        first_ids = [m.id for m in result['messages']]
        thread = row['thread_id']
    async with build(monkeypatch, db, tools=[create_session_read_tool()]) as second:
        storage = second._storage
        assert await second.arestore_session(session) == session
        assert second._sessions.require(session).get_thread_id() == thread
        later = await second.arun(session, [('user', 'continue')])
        assert [m.id for m in later['messages'][:4]] == first_ids
        assert sum(isinstance(m, ToolMessage) for m in later['messages']) == 1
        assert any(m.content == 'SYNTHETIC_TOOL_RESULT' for m in later['messages'])


@async_test
async def test_start_write_failure_calls_no_model_and_still_cleans_up(monkeypatch, tmp_path):
    model = ToolModel(responses=[AIMessage(content='unused')])
    async with build(monkeypatch, tmp_path / 'sessions.sqlite', model) as app:
        storage = app._storage
        session = await app.acreate_session('A')
        events = []
        app.subscribe_events(events.append)
        def fail(*_args):
            raise SessionPersistenceError('safe')
        monkeypatch.setattr(storage.sessions, 'mark_running', fail)
        with pytest.raises(SessionPersistenceError):
            await app.arun(session, [('user', 'q')])
        assert not any(e.event_type == 'model_call_started' for e in events)
        assert events[-1].data.status == 'failed'
        assert not app._sessions.require(session).describe_session()['busy']


@async_test
async def test_final_save_failure_has_no_completed_and_blocks_uncertain_state(monkeypatch, tmp_path):
    async with build(monkeypatch, tmp_path / 'sessions.sqlite') as app:
        storage = app._storage
        session = await app.acreate_session('A')
        events = []
        app.subscribe_events(events.append)
        def fail(*_args, **_kwargs):
            raise SessionPersistenceError('safe')
        monkeypatch.setattr(storage.runs, 'finish_run', fail)
        with pytest.raises(SessionPersistenceError):
            await app.arun(session, [('user', 'q')])
        assert storage.sessions.get(session)['state'] == 'running'
        assert events[-1].data.status == 'failed'
        assert not app._sessions.require(session).describe_session()['busy']
        with pytest.raises(SessionPersistenceError):
            await app.arun(session, [('user', 'must not execute')])


@pytest.mark.parametrize('state', ['running', 'failed', 'interrupted', 'review_required'])
@async_test
async def test_restart_rejects_restricted_state(monkeypatch, tmp_path, state):
    db = tmp_path / 'sessions.sqlite'
    async with build(monkeypatch, db) as app:
        storage = app._storage
        session = await app.acreate_session('A')
        storage.runs.finish_run(session, state, None, '', state)
    async with build(monkeypatch, db) as app:
        storage = app._storage
        with pytest.raises(SessionPersistenceError):
            await app.arestore_session(session)
        assert session not in app._sessions._sessions


@async_test
async def test_mismatching_checkpoint_is_not_silently_ignored(monkeypatch, tmp_path):
    db = tmp_path / 'sessions.sqlite'
    async with build(monkeypatch, db) as app:
        storage = app._storage
        session = await app.acreate_session('A')
        await app.arun(session, [('user', 'q')])
        storage.runs.finish_run(session, 'ready', 'wrong-checkpoint', '', 'completed')
    async with build(monkeypatch, db) as app:
        storage = app._storage
        with pytest.raises(SessionPersistenceError):
            await app.arestore_session(session)


def test_storage_instance_lock_and_close(tmp_path):
    db = tmp_path / 'sessions.sqlite'
    with ProjectStore(db):
        assert db.stat().st_mode & 0o777 == 0o600
        with pytest.raises(SessionPersistenceError):
            ProjectStore(db)
    with ProjectStore(db) as storage:
        assert storage.sessions.list_sessions() == []


@pytest.mark.parametrize('mode', [0o644, 0o666])
def test_existing_database_and_lock_load_without_private_mode_requirement(tmp_path, mode):
    db = tmp_path / 'sessions.sqlite'
    with ProjectStore(db):
        pass
    lock = db.with_name(db.name + '.lock')
    db.chmod(mode)
    lock.chmod(mode)
    before = db.read_bytes()
    with ProjectStore(db) as storage:
        assert storage.sessions.list_sessions() == []
    assert db.read_bytes() == before
    assert db.stat().st_mode & 0o777 == mode
    assert lock.stat().st_mode & 0o777 == mode


@async_test
async def test_cleanup_failure_never_leaves_session_ready(monkeypatch, tmp_path):
    from naiyue_test_agent.application.sessions.agent_session import AgentSession
    async with build(monkeypatch, tmp_path / 'sessions.sqlite') as app:
        storage = app._storage
        session = await app.acreate_session('A')
        def broken_cleanup(*_a, **_k):
            raise ValueError('PRIVATE_CLEANUP')
        monkeypatch.setattr(AgentSession, '_cleanup_run', broken_cleanup)
        with pytest.raises(ValueError):
            await app.arun(session, [('user', 'q')])
        assert storage.sessions.get(session)['state'] != 'ready'
        assert not app._sessions.require(session).describe_session()['busy']


@async_test
async def test_failed_run_keeps_last_confirmed_checkpoint(monkeypatch, tmp_path):
    class Flaky(ToolModel):
        attempts: int = 0
        def _generate(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 2:
                raise RuntimeError('PRIVATE_FAILURE')
            return super()._generate(*args, **kwargs)
    async with build(monkeypatch, tmp_path / 'sessions.sqlite', Flaky(responses=[AIMessage(content='saved')])) as app:
        storage = app._storage
        session = await app.acreate_session('A')
        await app.arun(session, [('user', 'one')])
        checkpoint = storage.sessions.get(session)['checkpoint_id']
        with pytest.raises(RuntimeError):
            await app.arun(session, [('user', 'two')])
        assert storage.sessions.get(session)['state'] == 'failed'
        assert storage.sessions.get(session)['checkpoint_id'] == checkpoint


@async_test
async def test_primary_model_failure_survives_secondary_store_failure(monkeypatch, tmp_path, caplog):
    primary = TimeoutError('PRIVATE_PRIMARY')
    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise primary
    async with build(monkeypatch, tmp_path / 'sessions.sqlite', Failing(responses=[])) as app:
        storage = app._storage
        session = await app.acreate_session('A')
        def fail(*_a, **_k):
            raise OSError('PRIVATE_SECONDARY')
        monkeypatch.setattr(storage.runs, 'finish_run', fail)
        with pytest.raises(TimeoutError) as caught:
            await app.arun(session, [('user', 'q')])
        assert caught.value is primary
        assert storage.sessions.get(session)['state'] == 'running'
        assert 'session_persistence_secondary_failed' in caplog.text
        assert 'PRIVATE_' not in caplog.text


@async_test
async def test_create_failure_does_not_install_memory_session(monkeypatch, tmp_path):
    async with build(monkeypatch, tmp_path / 'sessions.sqlite') as app:
        storage = app._storage
        storage.database.connection.execute('PRAGMA query_only=1')
        with pytest.raises(SessionPersistenceError):
            await app.acreate_session('A')
        assert app._sessions._sessions == {} and storage.sessions.list_sessions() == []


@async_test
async def test_approval_can_resume_in_process_but_is_blocked_after_restart(monkeypatch, tmp_path):
    db = tmp_path / 'sessions.sqlite'
    root = tmp_path / 'work'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('SYNTHETIC_APPROVED')
    async with build(monkeypatch, db, ToolModel(responses=[
            AIMessage(content='', tool_calls=[{'name': 'read', 'args': {'path': str(outside)}, 'id': 'c'}]),
            AIMessage(content='approved'),
        ]), [create_session_read_tool()]) as app:
        storage = app._storage
        session = await app.acreate_workspace_session('A', root)
        paused = await app.arun(session, [('user', 'read outside')])
        assert paused['approval_requests'] and storage.sessions.get(session)['state'] == 'interrupted'
        done = await app.aresume_tool_approval(session, ['approve'])
        assert done['messages'][-1].content == 'approved' and storage.sessions.get(session)['state'] == 'ready'
    async with build(monkeypatch, db, tools=[create_session_read_tool()]) as app:
        storage = app._storage
        assert await app.arestore_session(session) == session


@async_test
async def test_compacted_summary_remains_actual_context_after_reopen(monkeypatch, tmp_path):
    from test_context_compaction import _model
    db = tmp_path / 'sessions.sqlite'
    a = _model([AIMessage(content='one'), AIMessage(content='two')], window=10000)
    b = _model([AIMessage(content='final')], window=300, output=10, reserve=250, keep=10,
               summaries=[AIMessage(content='SYNTHETIC_SUMMARY')], model_id='B')
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': a, 'B': b}))
    async with open_async_persistent_application('unused', database_path=db, tools=[], system_prompt='', response_format=None) as app:
        storage = app._storage
        session = await app.acreate_session('A')
        await app.arun(session, [('user', 'x' * 100)])
        await app.arun(session, [('user', 'y' * 100)])
        original_ids = [m.id for m in await app.aget_messages(session)]
        await app.aselect_model(session, 'B')
        compressed = await app.arun(session, [('user', 'third')])
        first = compressed['messages'][0]
        assert first.additional_kwargs.get('naiyue_compaction_summary') is True
        archived_ids = [r[0] for r in storage.database.connection.execute(
            'SELECT message_id FROM naiyue_chat_messages WHERE session_id=? ORDER BY sequence', (session,))]
        assert archived_ids[:4] == original_ids and first.id in archived_ids
    async with open_async_persistent_application('unused', database_path=db, tools=[], system_prompt='', response_format=None) as app:
        storage = app._storage
        await app.arestore_session(session)
        snapshot = await app._agent.aget_state({'configurable': {'thread_id': storage.sessions.get(session)['thread_id']}})
        assert snapshot.values['messages'][0].id == first.id
        assert snapshot.values['messages'][0].content == first.content
        assert storage.sessions.get(session)['model_id'] == 'B'
        assert [r[0] for r in storage.database.connection.execute(
            'SELECT message_id FROM naiyue_chat_messages WHERE session_id=? ORDER BY sequence', (session,))] == archived_ids


def test_symlink_and_unknown_schema_are_rejected(tmp_path):
    target = tmp_path / 'target.txt'
    target.write_text('UNRELATED_CONTENT')
    alias = tmp_path / 'linked.sqlite'
    alias.symlink_to(target)
    with pytest.raises(SessionPersistenceError):
        ProjectStore(alias)
    assert target.read_text() == 'UNRELATED_CONTENT'
    db = tmp_path / 'sessions.sqlite'
    with ProjectStore(db) as storage:
        storage.database.connection.execute('UPDATE naiyue_store_info SET version=99')
        storage.database.connection.commit()
    with pytest.raises(SessionPersistenceError):
        ProjectStore(db)


def test_nonregular_wal_is_rejected_before_database_open(tmp_path):
    db = tmp_path / 'sessions.sqlite'
    with ProjectStore(db):
        pass
    before = db.read_bytes()
    wal = tmp_path / 'sessions.sqlite-wal'
    wal.mkdir()
    unrelated = wal / 'unrelated.txt'
    unrelated.write_bytes(b'UNRELATED')
    with pytest.raises(SessionPersistenceError):
        ProjectStore(db)
    assert unrelated.read_bytes() == b'UNRELATED' and db.read_bytes() == before


def test_existing_valid_wal_and_shm_load_without_private_mode_requirement(tmp_path):
    db = tmp_path / 'sessions.sqlite'
    with ProjectStore(db):
        pass
    keeper = sqlite3.connect(db)
    try:
        keeper.execute('PRAGMA journal_mode=WAL')
        keeper.execute('CREATE TABLE permission_probe(value TEXT)')
        keeper.execute('INSERT INTO permission_probe(value) VALUES(?)', ('saved',))
        keeper.commit()
        for suffix in ('-wal', '-shm'):
            db.with_name(db.name + suffix).chmod(0o644)
        with ProjectStore(db) as storage:
            assert storage.sessions.list_sessions() == []
            row = storage.database.connection.execute('SELECT value FROM permission_probe').fetchone()
            assert row['value'] == 'saved'
        for suffix in ('-wal', '-shm'):
            assert db.with_name(db.name + suffix).stat().st_mode & 0o777 == 0o644
    finally:
        keeper.close()
