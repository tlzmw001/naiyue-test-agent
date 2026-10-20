"""Deterministic deadlines, claims and owner generation during idle reclamation."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.types import ListToolsResult

from naiyue_test_agent.mcp_client.owner import MCPResourceError
from test_mcp_lifecycle import fake_connections, manager, reached


async def use(resources, session='A'):
    async with resources.borrow(session, 'root', 'local') as lease:
        return lease.client


def clock(resources):
    now = [100.0]
    resources._clock = lambda: now[0]
    return now


def expire(resources):
    for key, (owner, handle, deadline) in tuple(resources._idle_handles.items()):
        resources._expire_idle(key, owner, deadline)


async def settled(resources):
    await reached(lambda: not resources._idle_tasks)


def test_default_zero_never_schedules_idle_close(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        resources = manager()
        try:
            first = await use(resources)
            assert resources._idle_handles == {} and resources._idle_tasks == {}
            assert await use(resources) is first and not records[0].closed.is_set()
        finally:
            await resources.aclose()
    asyncio.run(scenario())


def test_deadline_boundary_and_new_generation_ignore_stale_callback(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        resources = manager(idle_timeout_seconds=10)
        now = clock(resources)
        try:
            first = await use(resources)
            key, (owner, _, deadline) = next(iter(resources._idle_handles.items()))
            assert deadline == 110
            now[0] = 109
            expire(resources)
            assert not records[0].closed.is_set()
            now[0] = 110
            expire(resources)
            await settled(resources)
            assert records[0].closed.is_set() and resources._owners == {}
            second = await use(resources)
            assert second is not first
            resources._expire_idle(key, owner, deadline)
            assert not records[1].closed.is_set()
        finally:
            await resources.aclose()
        assert not resources._idle_handles and not resources._idle_tasks
    asyncio.run(scenario())


def test_session_occupation_pauses_and_restarts_full_idle_period(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        resources = manager(idle_timeout_seconds=10)
        now = clock(resources)
        try:
            await use(resources)
            resources.set_session_occupied('A', True)
            assert not resources._idle_handles
            now[0] = 500
            expire(resources)
            assert not records[0].closed.is_set()
            resources.set_session_occupied('A', False)
            assert next(iter(resources._idle_handles.values()))[2] == 510
            now[0] = 510
            expire(resources)
            await settled(resources)
            assert records[0].closed.is_set()
        finally:
            await resources.aclose()
    asyncio.run(scenario())


def test_waiting_borrow_claim_wins_before_idle_callback(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        resources = manager(idle_timeout_seconds=10)
        now = clock(resources)
        try:
            first = await use(resources)
            key, (owner, _, deadline) = next(iter(resources._idle_handles.items()))
            async with resources.borrow('B', 'root', 'local'):
                queued = asyncio.create_task(use(resources))
                await reached(lambda: queued in resources._requests)
                now[0] = deadline
                resources._expire_idle(key, owner, deadline)
                assert not records[0].closed.is_set()
            assert await queued is first
            assert next(v[2] for k, v in resources._idle_handles.items() if k[0] == 'A') == deadline + 10
        finally:
            await resources.aclose()
    asyncio.run(scenario())


def test_new_borrow_waits_for_committed_close_then_creates_once(monkeypatch):
    from naiyue_test_agent.mcp_client import connections
    async def scenario():
        closing, finish = asyncio.Event(), asyncio.Event()
        records = []
        @asynccontextmanager
        async def connect(*args, **kwargs):
            async def listing(**kwargs): return ListToolsResult(tools=[])
            client = SimpleNamespace(session=SimpleNamespace(list_tools=listing))
            records.append(client)
            try:
                yield client
            finally:
                if len(records) == 1:
                    closing.set()
                    await finish.wait()
        monkeypatch.setattr(connections, 'open_connection', connect)
        resources = manager(idle_timeout_seconds=10, max_concurrency=2)
        now = clock(resources)
        try:
            first = await use(resources)
            now[0] = 110
            expire(resources)
            await closing.wait()
            queued = asyncio.create_task(use(resources))
            await reached(lambda: queued in resources._requests)
            assert not queued.done() and len(records) == 1
            finish.set()
            assert await queued is not first and len(records) == 2
            await settled(resources)
            assert len(resources._owners) == 1
        finally:
            finish.set()
            await resources.aclose()
    asyncio.run(scenario())


def test_failed_idle_close_blocks_replacement_and_is_reported_on_shutdown(monkeypatch, caplog):
    async def scenario():
        records = fake_connections(monkeypatch, exit_error=OSError('PRIVATE body'))
        resources = manager(idle_timeout_seconds=10)
        now = clock(resources)
        await use(resources)
        now[0] = 110
        expire(resources)
        await settled(resources)
        with pytest.raises(MCPResourceError, match='mcp_previous_cleanup_failed'):
            await use(resources)
        assert len(records) == 1
        with pytest.raises(ExceptionGroup):
            await resources.aclose()
        assert not resources._idle_handles and not resources._idle_tasks
    asyncio.run(scenario())
    assert 'mcp_idle_cleanup_failed' in caplog.text and 'PRIVATE body' not in caplog.text


def test_real_event_loop_timer_is_wired_and_closes_owner(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        resources = manager(idle_timeout_seconds=.05)
        try:
            await use(resources)
            async with asyncio.timeout(5):
                await records[0].closed.wait()
            await settled(resources)
            assert not resources._owners
        finally:
            await resources.aclose()
    asyncio.run(scenario())


def test_other_sessions_expire_independently_and_scope_release_cancels_timers(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        resources = manager(idle_timeout_seconds=10)
        now = clock(resources)
        try:
            await use(resources)
            await use(resources, 'B')
            resources.set_session_occupied('A', True)
            now[0] = 110
            expire(resources)
            await settled(resources)
            assert not records[0].closed.is_set() and records[1].closed.is_set()
            resources.set_session_occupied('A', False)
            assert resources._idle_handles
            await resources.release_session('A')
            assert not resources._idle_handles and not resources._active_sessions
            assert 'A' not in resources._activity_revisions
        finally:
            await resources.aclose()
    asyncio.run(scenario())


def test_late_worker_release_cannot_clear_a_new_run_occupation(monkeypatch):
    from threading import Thread
    async def scenario():
        fake_connections(monkeypatch)
        resources = manager(idle_timeout_seconds=10)
        notifications = []
        original = resources._loop.call_soon_threadsafe
        def queued(callback, *args, **kwargs):
            if callback == resources._apply_session_occupied:
                notifications.append((callback, args))
            else:
                return original(callback, *args, **kwargs)
        try:
            await use(resources)
            resources.set_session_occupied('A', True)
            with monkeypatch.context() as patch:
                patch.setattr(resources._loop, 'call_soon_threadsafe', queued)
                thread = Thread(target=lambda: resources.set_session_occupied('A', False))
                thread.start()
                thread.join(timeout=5)
                assert not thread.is_alive()
            resources.set_session_occupied('A', True)
            callback, args = notifications.pop()
            callback(*args)
            assert 'A' in resources._active_sessions and not resources._idle_handles
        finally:
            await resources.aclose()
    asyncio.run(scenario())


def test_close_waits_for_background_retirement_despite_caller_cancellation(monkeypatch):
    from naiyue_test_agent.mcp_client import connections
    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()
        @asynccontextmanager
        async def connect(*args, **kwargs):
            async def listing(**kwargs): return ListToolsResult(tools=[])
            try:
                yield SimpleNamespace(session=SimpleNamespace(list_tools=listing))
            finally:
                entered.set()
                await finish.wait()
        monkeypatch.setattr(connections, 'open_connection', connect)
        resources = manager(idle_timeout_seconds=10)
        now = clock(resources)
        await use(resources)
        now[0] = 110
        expire(resources)
        await entered.wait()
        closing = asyncio.create_task(resources.aclose())
        await reached(lambda: resources._closing)
        closing.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        await resources.aclose()
        assert not resources._idle_tasks and not resources._idle_handles and not resources._owners
    asyncio.run(scenario())
