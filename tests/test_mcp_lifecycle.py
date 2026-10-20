"""Event-synchronized connection ownership, borrowing and failure boundaries."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.types import ListToolsResult, Tool


def fake_connections(monkeypatch, *, enter_gate=None, exit_error=None):
    from naiyue_test_agent.mcp_client import connections
    records = []
    @asynccontextmanager
    async def connect(settings, network, **kwargs):
        record = SimpleNamespace(entered=asyncio.Event(), closed=asyncio.Event(), enter_task=asyncio.current_task(),
                                 exit_task=None, count=0, environment=kwargs.get('environment'))
        records.append(record)
        record.entered.set()
        try:
            if enter_gate is not None:
                await enter_gate.wait()
            async def listing(**kwargs):
                return ListToolsResult(tools=[Tool(name='counter', inputSchema={'type': 'object'})])
            record.session = SimpleNamespace(list_tools=listing)
            yield record
        finally:
            record.exit_task = asyncio.current_task()
            record.closed.set()
            if exit_error is not None:
                raise exit_error
    monkeypatch.setattr(connections, 'open_connection', connect)
    return records


def manager(**options):
    from naiyue_test_agent.mcp_client.resources import MCPSessionResources
    from naiyue_test_agent.mcp_client.settings import MCPSettings
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    settings = MCPSettings.model_validate({'servers': {'local': {'enabled': True, 'transport': 'stdio',
                    'command': 'unused', **options}}})
    return MCPSessionResources(settings, NetworkSettings())


async def reached(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0)


def test_same_scope_reuses_cross_task_while_other_scopes_are_independent(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        resources = manager()
        async def acquire(session, instance='root'):
            async with resources.borrow(session, instance, 'local') as lease:
                lease.client.count += 1
                return lease.client, lease.client.count, lease.config_identity
        try:
            a1 = await asyncio.create_task(acquire('A'))
            a2 = await asyncio.create_task(acquire('A'))
            b = await acquire('B')
            child = await acquire('A', 'root/tester/child')
            assert a1[0] is a2[0] and (a1[1], a2[1], b[1], child[1]) == (1, 2, 1, 1)
            assert a1[0] is not b[0] and a1[0] is not child[0]
            assert not any(record.closed.is_set() for record in records)
            await resources.release_instance('A', 'root/tester/child')
            assert records[2].closed.is_set() and not records[0].closed.is_set()
            await resources.release_session('A')
            assert records[0].closed.is_set() and not records[1].closed.is_set()
        finally:
            await resources.aclose()
        assert all(r.closed.is_set() and r.enter_task is r.exit_task for r in records)
    asyncio.run(scenario())


def test_concurrent_first_borrow_only_opens_once_and_one_waiter_cancel_isolated(monkeypatch):
    async def scenario():
        gate = asyncio.Event()
        records = fake_connections(monkeypatch, enter_gate=gate)
        resources = manager(max_concurrency=2)
        async def acquire():
            async with resources.borrow('A', 'root', 'local') as lease:
                return lease.client
        a = asyncio.create_task(acquire())
        b = asyncio.create_task(acquire())
        try:
            await reached(lambda: len(records) == 1)
            a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await a
            assert not records[0].closed.is_set()
            gate.set()
            assert await b is records[0]
            assert len(records) == 1
        finally:
            gate.set()
            await resources.aclose()
    asyncio.run(scenario())


def test_last_connecting_waiter_cancel_closes_owner(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch, enter_gate=asyncio.Event())
        resources = manager()
        async def acquire():
            async with resources.borrow('A', 'root', 'local'):
                pytest.fail('connection must not be ready')
        task = asyncio.create_task(acquire())
        await reached(lambda: bool(records))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert records[0].closed.is_set()
        assert records[0].enter_task is records[0].exit_task
        await resources.aclose()
    asyncio.run(scenario())


def test_server_capacity_applies_across_sessions_and_queued_cancel_keeps_owner(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        resources = manager()
        async with resources.borrow('A', 'root', 'local'):
            waiting = asyncio.Event()
            async def blocked():
                waiting.set()
                async with resources.borrow('B', 'root', 'local'):
                    pytest.fail('slot was not released')
            task = asyncio.create_task(blocked())
            await waiting.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(records) == 1
        async with resources.borrow('A', 'root', 'local') as lease:
            assert lease.client is records[0]
        await resources.aclose()
    asyncio.run(scenario())


def test_inflight_error_is_not_replayed_and_next_explicit_borrow_reconnects(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        resources = manager()
        original = OSError('PRIVATE remote request body')
        effects = []
        with pytest.raises(OSError) as caught:
            async with resources.borrow('A', 'root', 'local'):
                effects.append('submitted')
                raise original
        assert caught.value is original and effects == ['submitted']
        assert records[0].closed.is_set()
        async with resources.borrow('A', 'root', 'local'):
            assert len(records) == 2
        await resources.aclose()
    asyncio.run(scenario())


def test_close_cancels_inflight_rejects_new_borrows_and_is_idempotent(monkeypatch):
    async def scenario():
        from naiyue_test_agent.mcp_client.resources import MCPResourceError
        records = fake_connections(monkeypatch)
        resources = manager()
        entered = asyncio.Event()
        async def use():
            async with resources.borrow('A', 'root', 'local'):
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(use())
        await entered.wait()
        await asyncio.gather(resources.aclose(), resources.aclose())
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(MCPResourceError, match='closed'):
            async with resources.borrow('A', 'root', 'local'):
                pytest.fail('closed manager accepted work')
        assert all(r.closed.is_set() for r in records)
        await resources.aclose()
    asyncio.run(scenario())


def test_new_cleanup_error_preserves_primary_and_fails_final_close(monkeypatch, caplog):
    async def scenario():
        fake_connections(monkeypatch, exit_error=ValueError('PRIVATE cleanup'))
        resources = manager()
        primary = OSError('PRIVATE primary')
        with pytest.raises(OSError) as caught:
            async with resources.borrow('A', 'root', 'local'):
                raise primary
        assert caught.value is primary
        with pytest.raises(ExceptionGroup):
            await resources.aclose()
        assert 'mcp_cleanup_failed' in caplog.text and 'PRIVATE' not in caplog.text
    asyncio.run(scenario())
