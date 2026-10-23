"""One session's finish, recovery or shutdown never steals another owner."""

import asyncio
import sqlite3
from threading import Event

import pytest

from native_application_helpers import async_test
from naiyue_test_agent.application.errors import RunCapacityExceededError, SessionPersistenceError
from naiyue_test_agent.settings import AppSettings
from test_cross_session_concurrency import concurrent_app, drain, gated
from test_session_recovery import unfinished


@pytest.mark.parametrize('fail', [False, True])
@async_test
async def test_start_io_does_not_hold_coordination_lock_and_rolls_back_only_its_owner(monkeypatch, tmp_path, fail):
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite') as (app, model):
        a, b = [await app.acreate_session('A') for _ in range(2)]
        a_entered, _ = gated(model, a)
        entered, release = asyncio.Event(), Event()
        loop, start = asyncio.get_running_loop(), app._storage.sessions.mark_running
        primary = OSError('PRIVATE_START')
        def blocked(sid, rid):
            if sid == b:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
                if fail:
                    raise primary
            return start(sid, rid)
        monkeypatch.setattr(app._storage.sessions, 'mark_running', blocked)
        tasks = [asyncio.create_task(app.arun(a, [('user', a)]))]
        try:
            await asyncio.wait_for(a_entered.wait(), 5)
            tasks.append(asyncio.create_task(app.arun(b, [('user', b)])))
            await asyncio.wait_for(entered.wait(), 5)
            # A nonblocking acquire proves event-loop readers can use the same
            # lock while a database writer is waiting; no sleep race required.
            acquired = app._persistence._lock.acquire(blocking=False)
            try:
                assert acquired, 'database IO must not hold the event-loop coordination lock'
                assert set(app._persistence._active) == {a, b}
            finally:
                if acquired:
                    app._persistence._lock.release()
            release.set()
            if fail:
                with pytest.raises(OSError) as caught:
                    await tasks[1]
                assert caught.value is primary and app._storage.sessions.get(b)['state'] == 'ready'
            else:
                assert (await tasks[1])['messages'][-1].content == 'done:' + b
            assert set(app._persistence._active) == {a} and not tasks[0].done()
            assert not app._sessions.require(b).describe_session()['busy']
        finally:
            release.set()
            await drain(tasks, model)


@async_test
async def test_storage_reserves_writer_before_reading_its_write_transaction(monkeypatch, tmp_path):
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite') as (app, model):
        a, b = [await app.acreate_session('A') for _ in range(2)]
        await app.arun(a, [('user', a)])
        storage, interleaved = app._storage, []
        from naiyue_test_agent.storage import unit_of_work
        get = unit_of_work.session_row
        # Another connection commits exactly after the metadata read, without
        # timing assumptions. A reserved writer must exclude that commit.
        def get_then_write(connection, sid):
            row = get(connection, sid)
            if sid == a and not interleaved:
                with sqlite3.connect(storage.path, timeout=0) as competitor:
                    try:
                        competitor.execute("UPDATE naiyue_sessions SET title='competitor' WHERE session_id=?", (b,))
                    except sqlite3.OperationalError as error:
                        assert error.sqlite_errorname == 'SQLITE_BUSY'
                        interleaved.append('excluded')
                    else:
                        interleaved.append('committed')
            return row
        monkeypatch.setattr(unit_of_work, 'session_row', get_then_write)
        row = storage.sessions.get(a)
        storage.runs.finish_run(a, 'ready', row['checkpoint_id'], '', 'completed')
        assert interleaved == ['excluded']
        assert storage.sessions.get(b)['title'] is None and storage.sessions.get(a)['state'] == 'ready'


@pytest.mark.parametrize('outcome', ['error', 'cancel', 'stop', 'save_failure'])
@async_test
async def test_failure_or_stop_releases_only_its_session(monkeypatch, tmp_path, caplog, outcome):
    primary, save_error = RuntimeError('PRIVATE_MODEL'), OSError('PRIVATE_SAVE')
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite',
                              settings=AppSettings(max_active_runs=2)) as (app, model):
        a, b, c = [await app.acreate_session('A') for _ in range(3)]
        events, tasks = [], []
        app.subscribe_events(events.append)
        if outcome == 'error':
            model.responses[a] = [primary]
        original = app._storage.runs.finish_run
        def finish(sid, *args, **kwargs):
            if sid == a and outcome == 'save_failure':
                raise save_error
            return original(sid, *args, **kwargs)
        monkeypatch.setattr(app._storage.runs, 'finish_run', finish)
        try:
            for sid in (a, b):
                entered, _ = gated(model, sid)
                tasks.append(asyncio.create_task(app.arun(sid, [('user', sid)])))
                await asyncio.wait_for(entered.wait(), 5)
            b_run = app._persistence._active[b]
            await app._persistence.afinish(b, 'stale-owner', app._agent, None, {})
            assert app._persistence._active[b] == b_run
            if outcome == 'cancel':
                tasks[0].cancel('cancel only A')
                with pytest.raises(asyncio.CancelledError) as caught:
                    await tasks[0]
                assert caught.value.args == ('cancel only A',)
            elif outcome == 'stop':
                app.steer(a, 'retain A steering')
                app.follow_up(a, 'retain A follow-up')
                assert app.request_stop(a) == ['retain A steering', 'retain A follow-up']
                assert (await tasks[0])['run_stopped']
            else:
                model.gates[a][1].set()
                expected = SessionPersistenceError if outcome == 'save_failure' else RuntimeError
                with pytest.raises(expected) as caught:
                    await tasks[0]
                if outcome == 'error':
                    assert caught.value is primary
                else:
                    assert caught.value.__cause__ is save_error
            assert not app._sessions.require(a).describe_session()['busy']
            assert not tasks[1].done() and app._sessions.require(b).describe_session()['busy']
            assert app._persistence._active == {b: b_run}
            assert app._storage.sessions.get(b)['state'] == 'running'
            assert not any(e.session_id == b and e.event_type == 'run_finished' for e in events)
            await app.arun(c, [('user', c)])
            assert app._persistence._active == {b: b_run}
            assert app._storage.sessions.get(c)['state'] == 'ready'
            model.gates[b][1].set()
            assert (await tasks[1])['messages'][-1].content == 'done:' + b
            assert app._storage.sessions.get(b)['state'] == 'ready' and app._persistence._active == {}
            assert app._persistence._blocked == ({a} if outcome == 'save_failure' else set())
            assert [e.data.status for e in events if e.session_id == b and e.event_type == 'run_finished'] == ['completed']
        finally:
            await drain(tasks, model)
    assert 'PRIVATE_MODEL' not in caplog.text and 'PRIVATE_SAVE' not in caplog.text
    assert not any(word in caplog.text for word in ('observation_failed', 'run_event_publish_failed'))


@async_test
async def test_required_save_holds_capacity_while_another_session_runs(monkeypatch, tmp_path):
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite',
                              settings=AppSettings(max_active_runs=2)) as (app, model):
        a, b, c = [await app.acreate_session('A') for _ in range(3)]
        entered, release = asyncio.Event(), Event()
        loop, finish = asyncio.get_running_loop(), app._storage.runs.finish_run
        def pause(sid, *args, **kwargs):
            if sid == a:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
            return finish(sid, *args, **kwargs)
        monkeypatch.setattr(app._storage.runs, 'finish_run', pause)
        b_entered, _ = gated(model, b)
        tasks = [asyncio.create_task(app.arun(b, [('user', b)]))]
        try:
            await asyncio.wait_for(b_entered.wait(), 5)
            tasks.append(asyncio.create_task(app.arun(a, [('user', a)])))
            await asyncio.wait_for(entered.wait(), 5)
            owner = app._sessions._sessions[a]
            assert owner.is_busy() and not owner._run.controls_open
            with pytest.raises(RunCapacityExceededError):
                await app.arun(c, [('user', c)])
            release.set()
            await tasks[1]
            await app.arun(c, [('user', c)])
            assert set(app._persistence._active) == {b} and not tasks[0].done()
        finally:
            release.set()
            await drain(tasks, model)


@async_test
async def test_running_a_allows_idle_b_recovery_restore_and_deletion(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    cold, checkpoint, _ = await unfinished(monkeypatch, path)
    async with concurrent_app(monkeypatch, path) as (app, model):
        idle = await app.acreate_session('A')
        await app.arun(idle, [('user', idle)])
    async with concurrent_app(monkeypatch, path) as (app, model):
        a = await app.acreate_session('A')
        entered, _ = gated(model, a)
        task = asyncio.create_task(app.arun(a, [('user', a)]))
        recovery = None
        recovery_entered, release = asyncio.Event(), asyncio.Event()
        update = app._agent.aupdate_state
        async def pause(*args, **kwargs):
            recovery_entered.set()
            await release.wait()
            return await update(*args, **kwargs)
        try:
            await asyncio.wait_for(entered.wait(), 5)
            monkeypatch.setattr(app._agent, 'aupdate_state', pause)
            recovery = asyncio.create_task(app.arecover_session(cold,
                checkpoint_id=checkpoint.config['configurable']['checkpoint_id'], action='continue'))
            await asyncio.wait_for(recovery_entered.wait(), 5)
            assert app._persistence._recovering == {cold}
            with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
                app._persistence.start(cold, 'duplicate')
            with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
                await app.arestore_session(cold)
            with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
                await app.adelete_session(cold)
            assert await app.arestore_session(idle) == idle
            await app.adelete_session(idle)
            assert app._persistence._recovering == {cold}
            release.set()
            assert (await recovery)['state'] == 'ready'
            assert app._persistence._recovering == set()
            await app.adelete_session(cold)
            assert set(app._persistence._active) == {a} and not task.done()
            with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
                app._persistence.start(a, 'duplicate')
            with pytest.raises(SessionPersistenceError):
                await app.adelete_session(a)
        finally:
            release.set()
            await drain([task, *([recovery] if recovery else [])], model)


@async_test
async def test_close_waits_for_every_root_before_catalog_close(monkeypatch, tmp_path):
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite') as (app, model):
        ids = [await app.acreate_session('A') for _ in range(2)]
        tasks, closed = [], []
        close = app._catalog.aclose
        async def closing():
            closed.append([app._sessions.require(sid).describe_session()['busy'] for sid in ids])
            assert app._persistence._active == {}
            await close()
        monkeypatch.setattr(app._catalog, 'aclose', closing)
        try:
            for sid in ids:
                entered, _ = gated(model, sid)
                tasks.append(asyncio.create_task(app.arun(sid, [('user', sid)])))
                await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(app.aclose(), 5)
            assert all(result['run_stopped'] for result in await asyncio.gather(*tasks))
            assert closed == [[False, False]] and app._operations == {}
            assert all(app._storage.sessions.get(sid)['state'] == 'ready' for sid in ids)
        finally:
            await drain(tasks, model)
