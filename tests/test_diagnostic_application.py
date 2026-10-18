"""Production assembly captures terminal events after required saves and drains on close."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent.application.session_storage import SessionStorage
from test_async_application import StreamingToolModel, call
from test_persistent_sessions import build


@async_test
async def test_real_tool_error_is_recorded_without_failing_root_or_copying_bodies(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import create_session_read_tool
    path = tmp_path / 'state.sqlite'
    model = StreamingToolModel(replies=[call('read', {'path': 'missing.txt'}), AIMessage(content='PRIVATE_ANSWER')])
    async with build(monkeypatch, path, model, [create_session_read_tool()]) as app:
        sid = await app.acreate_workspace_session('A', tmp_path)
        result = await app.arun(sid, [('user', 'PRIVATE_PROMPT')])
        assert result['messages'][-1].content == 'PRIVATE_ANSWER'
        assert app._diagnostics is not None
        assert hasattr(app, 'alist_diagnostic_runs') and hasattr(app, 'aget_diagnostic_events')
    assert app._diagnostics._task.done() and not app._diagnostics._runs
    with SessionStorage(path) as store:
        runs = store.read_diagnostic_runs(sid)['runs']
        assert len(runs) == 1 and runs[0]['status'] == 'completed'
        page = store.read_diagnostic_events(sid, runs[0]['run_id'], limit=100)
        assert not page['summary']['capture_incomplete']
        assert any(e['event_type'] == 'tool_call_finished' and e['data']['outcome'] == 'error_result' for e in page['events'])
        assert any(e['event_type'] == 'model_invocation_finished' for e in page['events'])
        assert 'PRIVATE' not in json.dumps(page)
        assert store.connection.execute('SELECT COUNT(*) FROM naiyue_chat_messages WHERE session_id=?', (sid,)).fetchone()[0] == 4
        queries = ('SELECT COUNT(*) FROM naiyue_chat_messages', 'SELECT COUNT(*) FROM naiyue_model_invocations',
                   'SELECT COUNT(*) FROM checkpoints', 'SELECT COUNT(*) FROM writes')
        before = [store.connection.execute(sql).fetchone()[0] for sql in queries]
        from datetime import datetime, timedelta, timezone
        from test_diagnostic_storage import limits
        store.prune_diagnostics(limits(), now=datetime.now(timezone.utc) + timedelta(days=31))
        assert store.read_diagnostic_runs(sid)['runs'] == []
        after = [store.connection.execute(sql).fetchone()[0] for sql in queries]
        assert after == before


@pytest.mark.parametrize('model_error', [False, True])
@async_test
async def test_diagnostic_io_failure_preserves_result_or_original_exception(monkeypatch, tmp_path, caplog, model_error):
    original = TimeoutError('PRIVATE_MODEL_FAILURE')
    model = StreamingToolModel(replies=[original if model_error else AIMessage(content='done')])
    path = tmp_path / 'state.sqlite'
    async with build(monkeypatch, path, model) as app:
        sid = await app.acreate_session('A')
        def fail(*args):
            raise OSError('PRIVATE_DIAGNOSTIC_FAILURE')
        monkeypatch.setattr(app._storage, 'save_diagnostic_event', fail)
        if model_error:
            with pytest.raises(TimeoutError) as caught:
                await app.arun(sid, [('user', 'task')])
            assert caught.value is original
        else:
            assert (await app.arun(sid, [('user', 'task')]))['messages'][-1].content == 'done'
        assert sid not in app._persistence._blocked
    with SessionStorage(path) as store:
        run, = store.read_diagnostic_runs(sid)['runs']
        assert run['status'] == ('failed' if model_error else 'completed')
        assert run['capture_incomplete'] and run['reason'] == 'storage_error'
    assert 'diagnostic_save_failed' in caplog.text and 'PRIVATE_DIAGNOSTIC_FAILURE' not in caplog.text


@async_test
async def test_stop_terminal_is_saved_and_deleted_session_cannot_be_recreated_by_late_observation(monkeypatch, tmp_path):
    from test_diagnostic_storage import fact, limits
    model = StreamingToolModel(replies=[], entered=asyncio.Event(), release=asyncio.Event())
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(sid, [('user', 'task')]))
        await asyncio.wait_for(model.entered.wait(), 5)
        app.request_stop(sid)
        result = await asyncio.wait_for(task, 5)
        assert result['run_stopped']
        await app._diagnostics.aclose()
        run, = (await app.alist_diagnostic_runs(sid))['runs']
        assert run['status'] == 'stopped'
        await app.adelete_session(sid)
        assert app._storage._read('SELECT * FROM naiyue_diagnostic_runs') == []
        assert app._storage.save_diagnostic_event(fact(sid=sid), limits()) is False


@async_test
async def test_terminal_write_failure_keeps_success_and_reopen_marks_capture_only(monkeypatch, tmp_path, caplog):
    path = tmp_path / 'state.sqlite'
    model = StreamingToolModel(replies=[AIMessage(content='done')])
    async with build(monkeypatch, path, model) as app:
        sid = await app.acreate_session('A')
        def fail(*args, **kwargs):
            raise OSError('PRIVATE_TERMINAL_IO')
        monkeypatch.setattr(app._storage, 'finish_diagnostic_run', fail)
        assert (await app.arun(sid, [('user', 'task')]))['messages'][-1].content == 'done'
    with SessionStorage(path) as store:
        from test_diagnostic_storage import limits
        metadata = store.get(sid)
        assert metadata['state'] == 'ready'
        store.prepare_diagnostics(limits())
        run, = store.read_diagnostic_runs(sid)['runs']
        assert run['capture_state'] == 'interrupted' and run['status'] is None
        assert run['capture_incomplete'] and store.get(sid) == metadata
    assert 'diagnostic_finalize_failed' in caplog.text and 'PRIVATE_TERMINAL_IO' not in caplog.text


@async_test
async def test_checkpoint_schema_is_ready_before_diagnostic_subscription(monkeypatch, tmp_path):
    from naiyue_test_agent.application.diagnostic_persistence import DiagnosticPersistence
    original = DiagnosticPersistence.__aenter__
    checked = []
    async def check(self):
        names = {r['name'] for r in self._storage._read(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('checkpoints','writes')")}
        assert names == {'checkpoints', 'writes'}
        checked.append(True)
        return await original(self)
    monkeypatch.setattr(DiagnosticPersistence, '__aenter__', check)
    async with build(monkeypatch, tmp_path / 'state.sqlite'):
        assert checked == [True]


@async_test
async def test_framework_lazy_wal_initialization_conflicts_with_already_open_metadata_write(tmp_path):
    import sqlite3
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        async with AsyncSqliteSaver.from_conn_string(str(store.path)) as saver:
            await saver.conn.execute('PRAGMA busy_timeout=0')
            store.connection.execute('BEGIN IMMEDIATE')
            try:
                with pytest.raises(sqlite3.OperationalError, match='database is locked'):
                    await saver.aget_tuple({'configurable': {'thread_id': 'probe'}})
            finally:
                store.connection.rollback()
