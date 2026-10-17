"""Persistent sessions keep framework state and application recovery boundaries."""

from pathlib import Path
import sqlite3

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.persistent import build_persistent_application
from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog
from test_application_run_events import ToolModel


def build(monkeypatch, storage, model=None, tools=None):
    model = model or ToolModel(responses=[AIMessage(content='answer')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    return build_persistent_application('unused', storage=storage, tools=tools or [],
                                        system_prompt='', response_format=None)


def test_close_reopen_preserves_ids_tool_history_and_has_no_old_tool_execution(monkeypatch, tmp_path):
    db = tmp_path / 'sessions.sqlite'
    (tmp_path / 'note.txt').write_text('SYNTHETIC_TOOL_RESULT')
    with SessionStorage(db) as storage:
        first = build(monkeypatch, storage, ToolModel(responses=[
            AIMessage(content='before', tool_calls=[{'name': 'read', 'args': {'path': 'note.txt'}, 'id': 't'}]),
            AIMessage(content='after'),
        ]), [create_session_read_tool()])
        session = first.create_workspace_session('A', tmp_path)
        result = first.run(session, [HumanMessage(content='original request', id='h')])
        row = storage.get(session)
        assert row['state'] == 'ready' and row['checkpoint_id']
        first_ids = [m.id for m in result['messages']]
        thread = row['thread_id']
    with SessionStorage(db) as storage:
        second = build(monkeypatch, storage, tools=[create_session_read_tool()])
        assert second.restore_session(session) == session
        assert second._sessions._sessions[session]['thread_id'] == thread
        later = second.run(session, [('user', 'continue')])
        assert [m.id for m in later['messages'][:4]] == first_ids
        assert sum(isinstance(m, ToolMessage) for m in later['messages']) == 1
        assert any(m.content == 'SYNTHETIC_TOOL_RESULT' for m in later['messages'])


def test_start_write_failure_calls_no_model_and_still_cleans_up(monkeypatch, tmp_path):
    with SessionStorage(tmp_path / 'sessions.sqlite') as storage:
        model = ToolModel(responses=[AIMessage(content='unused')])
        app = build(monkeypatch, storage, model)
        session = app.create_session('A')
        events = []
        app.subscribe_events(events.append)
        def fail(*_args):
            raise SessionPersistenceError('safe')
        monkeypatch.setattr(storage, 'mark_running', fail)
        with pytest.raises(SessionPersistenceError):
            app.run(session, [('user', 'q')])
        assert not any(e.event_type == 'model_call_started' for e in events)
        assert events[-1].data.status == 'failed'
        assert not app._sessions._sessions[session]['busy']


def test_final_save_failure_has_no_completed_and_blocks_uncertain_state(monkeypatch, tmp_path):
    with SessionStorage(tmp_path / 'sessions.sqlite') as storage:
        app = build(monkeypatch, storage)
        session = app.create_session('A')
        events = []
        app.subscribe_events(events.append)
        def fail(*_args, **_kwargs):
            raise SessionPersistenceError('safe')
        monkeypatch.setattr(storage, 'finish_run', fail)
        with pytest.raises(SessionPersistenceError):
            app.run(session, [('user', 'q')])
        assert storage.get(session)['state'] == 'running'
        assert events[-1].data.status == 'failed'
        assert not app._sessions._sessions[session]['busy']
        with pytest.raises(SessionPersistenceError):
            app.run(session, [('user', 'must not execute')])


@pytest.mark.parametrize('state', ['running', 'failed', 'interrupted', 'review_required'])
def test_restart_rejects_restricted_state(monkeypatch, tmp_path, state):
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db) as storage:
        app = build(monkeypatch, storage)
        session = app.create_session('A')
        storage.finish_run(session, state, None, '', state)
    with SessionStorage(db) as storage:
        app = build(monkeypatch, storage)
        with pytest.raises(SessionPersistenceError):
            app.restore_session(session)
        assert session not in app._sessions._sessions


def test_mismatching_checkpoint_is_not_silently_ignored(monkeypatch, tmp_path):
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db) as storage:
        app = build(monkeypatch, storage)
        session = app.create_session('A')
        app.run(session, [('user', 'q')])
        storage.finish_run(session, 'ready', 'wrong-checkpoint', '', 'completed')
    with SessionStorage(db) as storage:
        app = build(monkeypatch, storage)
        with pytest.raises(SessionPersistenceError):
            app.restore_session(session)


def test_storage_instance_lock_and_close(tmp_path):
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db):
        assert db.stat().st_mode & 0o777 == 0o600
        with pytest.raises(SessionPersistenceError):
            SessionStorage(db)
    with SessionStorage(db) as storage:
        assert storage.list_sessions() == []


@pytest.mark.parametrize('mode', [0o644, 0o666])
def test_existing_database_and_lock_load_without_private_mode_requirement(tmp_path, mode):
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db):
        pass
    lock = db.with_name(db.name + '.lock')
    db.chmod(mode)
    lock.chmod(mode)
    before = db.read_bytes()
    with SessionStorage(db) as storage:
        assert storage.list_sessions() == []
    assert db.read_bytes() == before
    assert db.stat().st_mode & 0o777 == mode
    assert lock.stat().st_mode & 0o777 == mode


def test_cleanup_failure_never_leaves_session_ready(monkeypatch, tmp_path):
    from naiyue_test_agent.application.session_service import SessionService
    with SessionStorage(tmp_path / 'sessions.sqlite') as storage:
        app = build(monkeypatch, storage)
        session = app.create_session('A')
        def broken_cleanup(*_a, **_k):
            raise ValueError('PRIVATE_CLEANUP')
        monkeypatch.setattr(SessionService, '_cleanup_run', broken_cleanup)
        with pytest.raises(ValueError):
            app.run(session, [('user', 'q')])
        assert storage.get(session)['state'] != 'ready'
        assert not app._sessions._sessions[session]['busy']


def test_failed_run_keeps_last_confirmed_checkpoint(monkeypatch, tmp_path):
    class Flaky(ToolModel):
        attempts: int = 0
        def _generate(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 2:
                raise RuntimeError('PRIVATE_FAILURE')
            return super()._generate(*args, **kwargs)
    with SessionStorage(tmp_path / 'sessions.sqlite') as storage:
        app = build(monkeypatch, storage, Flaky(responses=[AIMessage(content='saved')]))
        session = app.create_session('A')
        app.run(session, [('user', 'one')])
        checkpoint = storage.get(session)['checkpoint_id']
        with pytest.raises(RuntimeError):
            app.run(session, [('user', 'two')])
        assert storage.get(session)['state'] == 'failed'
        assert storage.get(session)['checkpoint_id'] == checkpoint


def test_primary_model_failure_survives_secondary_store_failure(monkeypatch, tmp_path, caplog):
    primary = TimeoutError('PRIVATE_PRIMARY')
    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise primary
    with SessionStorage(tmp_path / 'sessions.sqlite') as storage:
        app = build(monkeypatch, storage, Failing(responses=[]))
        session = app.create_session('A')
        def fail(*_a, **_k):
            raise OSError('PRIVATE_SECONDARY')
        monkeypatch.setattr(storage, 'finish_run', fail)
        with pytest.raises(TimeoutError) as caught:
            app.run(session, [('user', 'q')])
        assert caught.value is primary
        assert storage.get(session)['state'] == 'running'
        assert 'session_persistence_secondary_failed' in caplog.text
        assert 'PRIVATE_' not in caplog.text


def test_create_failure_does_not_install_memory_session(monkeypatch, tmp_path):
    with SessionStorage(tmp_path / 'sessions.sqlite') as storage:
        app = build(monkeypatch, storage)
        storage.connection.execute('PRAGMA query_only=1')
        with pytest.raises(SessionPersistenceError):
            app.create_session('A')
        assert app._sessions._sessions == {} and storage.list_sessions() == []


def test_approval_can_resume_in_process_but_is_blocked_after_restart(monkeypatch, tmp_path):
    db = tmp_path / 'sessions.sqlite'
    root = tmp_path / 'work'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('SYNTHETIC_APPROVED')
    with SessionStorage(db) as storage:
        app = build(monkeypatch, storage, ToolModel(responses=[
            AIMessage(content='', tool_calls=[{'name': 'read', 'args': {'path': str(outside)}, 'id': 'c'}]),
            AIMessage(content='approved'),
        ]), [create_session_read_tool()])
        session = app.create_workspace_session('A', root)
        paused = app.run(session, [('user', 'read outside')])
        assert paused['approval_requests'] and storage.get(session)['state'] == 'interrupted'
        done = app.resume_tool_approval(session, ['approve'])
        assert done['messages'][-1].content == 'approved' and storage.get(session)['state'] == 'ready'
    with SessionStorage(db) as storage:
        app = build(monkeypatch, storage, tools=[create_session_read_tool()])
        assert app.restore_session(session) == session


def test_compacted_summary_remains_actual_context_after_reopen(monkeypatch, tmp_path):
    from test_context_compaction import _model
    db = tmp_path / 'sessions.sqlite'
    a = _model([AIMessage(content='one'), AIMessage(content='two')], window=10000)
    b = _model([AIMessage(content='final')], window=300, output=10, reserve=250, keep=10,
               summaries=[AIMessage(content='SYNTHETIC_SUMMARY')], model_id='B')
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': a, 'B': b}))
    with SessionStorage(db) as storage:
        app = build_persistent_application('unused', storage=storage, tools=[], system_prompt='', response_format=None)
        session = app.create_session('A')
        app.run(session, [('user', 'x' * 100)])
        app.run(session, [('user', 'y' * 100)])
        app.select_model(session, 'B')
        compressed = app.run(session, [('user', 'third')])
        first = compressed['messages'][0]
        assert first.additional_kwargs.get('naiyue_compaction_summary') is True
    with SessionStorage(db) as storage:
        app = build_persistent_application('unused', storage=storage, tools=[], system_prompt='', response_format=None)
        app.restore_session(session)
        snapshot = app._agent.get_state({'configurable': {'thread_id': storage.get(session)['thread_id']}})
        assert snapshot.values['messages'][0].id == first.id
        assert snapshot.values['messages'][0].content == first.content
        assert storage.get(session)['model_id'] == 'B'


def test_symlink_and_unknown_schema_are_rejected(tmp_path):
    target = tmp_path / 'target.txt'
    target.write_text('UNRELATED_CONTENT')
    alias = tmp_path / 'linked.sqlite'
    alias.symlink_to(target)
    with pytest.raises(SessionPersistenceError):
        SessionStorage(alias)
    assert target.read_text() == 'UNRELATED_CONTENT'
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db) as storage:
        storage.connection.execute('UPDATE naiyue_store_info SET version=99')
        storage.connection.commit()
    with pytest.raises(SessionPersistenceError):
        SessionStorage(db)


def test_nonregular_wal_is_rejected_before_database_open(tmp_path):
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db):
        pass
    before = db.read_bytes()
    wal = tmp_path / 'sessions.sqlite-wal'
    wal.mkdir()
    unrelated = wal / 'unrelated.txt'
    unrelated.write_bytes(b'UNRELATED')
    with pytest.raises(SessionPersistenceError):
        SessionStorage(db)
    assert unrelated.read_bytes() == b'UNRELATED' and db.read_bytes() == before


def test_existing_valid_wal_and_shm_load_without_private_mode_requirement(tmp_path):
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db):
        pass
    keeper = sqlite3.connect(db)
    try:
        keeper.execute('PRAGMA journal_mode=WAL')
        keeper.execute('CREATE TABLE permission_probe(value TEXT)')
        keeper.execute('INSERT INTO permission_probe(value) VALUES(?)', ('saved',))
        keeper.commit()
        for suffix in ('-wal', '-shm'):
            db.with_name(db.name + suffix).chmod(0o644)
        with SessionStorage(db) as storage:
            assert storage.list_sessions() == []
            row = storage.connection.execute('SELECT value FROM permission_probe').fetchone()
            assert row['value'] == 'saved'
        for suffix in ('-wal', '-shm'):
            assert db.with_name(db.name + suffix).stat().st_mode & 0o777 == 0o644
    finally:
        keeper.close()
