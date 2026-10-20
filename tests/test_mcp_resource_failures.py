"""Cancellation, failure ownership and frozen connection identity."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from test_mcp_lifecycle import fake_connections, manager, reached


@pytest.mark.parametrize('external_cancel', [False, True])
def test_close_with_requested_control_distinguishes_owned_and_external_cancel(monkeypatch, external_cancel):
    from naiyue_test_agent.agent_runtime.run_control import RunControl
    async def scenario():
        fake_connections(monkeypatch)
        resources, control, entered = manager(), RunControl(), asyncio.Event()
        async def use():
            async with resources.borrow('A', 'root', 'local', control=control):
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(use())
        await entered.wait()
        control.mark_requested('close')
        if external_cancel:
            task.cancel()
        await resources.aclose()
        with pytest.raises(asyncio.CancelledError if external_cancel else ToolException):
            await task
        assert resources._requests == {} and resources._owners == {}
    asyncio.run(scenario())


@pytest.mark.parametrize('external_cancel', [False, True])
def test_close_waits_for_settling_borrower_without_overwriting_primary(monkeypatch, external_cancel):
    from naiyue_test_agent.agent_runtime.run_control import RunControl
    from naiyue_test_agent.mcp_client import connections
    from mcp.types import ListToolsResult
    async def scenario():
        closing, finish = asyncio.Event(), asyncio.Event()
        @asynccontextmanager
        async def connect(*args, **kwargs):
            async def listing(**kwargs): return ListToolsResult(tools=[])
            try:
                yield SimpleNamespace(session=SimpleNamespace(list_tools=listing))
            finally:
                closing.set()
                await finish.wait()
        monkeypatch.setattr(connections, 'open_connection', connect)
        resources, control = manager(), RunControl()
        primary = ToolException('original tool failure')
        async def use():
            async with resources.borrow('A', 'root', 'local', control=control):
                raise primary
        task = asyncio.create_task(use())
        await closing.wait()
        control.mark_requested('close')
        release = asyncio.create_task(resources.aclose())
        await reached(lambda: resources._closing)
        if external_cancel: task.cancel()
        finish.set()
        await release
        with pytest.raises(asyncio.CancelledError if external_cancel else ToolException) as caught:
            await task
        if not external_cancel: assert caught.value is primary
        assert resources._requests == {} and resources._shutdown_cancellations == {} and resources._settling == set()
    asyncio.run(scenario())


def test_cleanup_error_still_releases_slot_and_request_tracking(monkeypatch):
    async def scenario():
        from naiyue_test_agent.mcp_client.resources import MCPResourceError
        fake_connections(monkeypatch, exit_error=ValueError('PRIVATE cleanup'))
        resources = manager()
        with pytest.raises(MCPResourceError, match='cleanup_failed'):
            async with resources.borrow('A', 'root', 'local'):
                owner = next(iter(resources._owners.values()))
                owner.stop.set()
                await owner.task
        assert resources._requests == {} and resources._slots['local']._value == 1
        with pytest.raises(ExceptionGroup):
            await resources.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('phase', ['queued', 'connecting', 'inflight'])
def test_run_control_stops_each_borrow_phase_without_replaying(monkeypatch, phase):
    from naiyue_test_agent.agent_runtime.run_control import RunControl
    async def scenario():
        gate = asyncio.Event() if phase == 'connecting' else None
        records = fake_connections(monkeypatch, enter_gate=gate)
        resources = manager()
        control, started = RunControl(), asyncio.Event()
        effects = []
        async def use():
            started.set()
            async with resources.borrow('A', 'root', 'local', control=control):
                effects.append('one')
                await asyncio.Event().wait()
        if phase == 'queued':
            async with resources.borrow('B', 'root', 'local'):
                task = asyncio.create_task(use())
                await started.wait()
                control.request()
                with pytest.raises(ToolException, match='stopped'):
                    await task
                assert not records[0].closed.is_set()
        else:
            task = asyncio.create_task(use())
            await reached(lambda: bool(records) if phase == 'connecting' else bool(effects))
            control.request()
            with pytest.raises(ToolException, match='stopped'):
                await task
            assert records[0].closed.is_set()
        assert effects == (['one'] if phase == 'inflight' else [])
        await resources.aclose()
        assert resources._requests == {}
    asyncio.run(scenario())


def test_scope_release_rejects_new_calls_until_owner_has_closed(monkeypatch):
    async def scenario():
        from naiyue_test_agent.mcp_client import connections
        from naiyue_test_agent.mcp_client.resources import MCPResourceError
        from mcp.types import ListToolsResult
        closing, finish = asyncio.Event(), asyncio.Event()
        tasks = []
        @asynccontextmanager
        async def connect(*args, **kwargs):
            tasks.append(asyncio.current_task())
            async def listing(**kwargs):
                return ListToolsResult(tools=[])
            try:
                yield SimpleNamespace(session=SimpleNamespace(list_tools=listing))
            finally:
                closing.set()
                await finish.wait()
                tasks.append(asyncio.current_task())
        monkeypatch.setattr(connections, 'open_connection', connect)
        resources = manager()
        async with resources.borrow('A', 'root', 'local'):
            pass
        release = asyncio.create_task(resources.release_session('A'))
        await closing.wait()
        with pytest.raises(MCPResourceError, match='scope_closing'):
            async with resources.borrow('A', 'root', 'local'):
                pytest.fail('scope is still closing')
        release.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await release
        assert tasks[0] is tasks[1] and not resources._owners
        await resources.aclose()
    asyncio.run(scenario())


def test_explicit_credential_snapshot_and_identity_are_bound_to_application(monkeypatch):
    async def scenario():
        records = fake_connections(monkeypatch)
        monkeypatch.setenv('MCP_BOUND_KEY', 'first-PRIVATE')
        resources = manager(env={'SERVER_KEY': '${MCP_BOUND_KEY}'})
        monkeypatch.setenv('MCP_BOUND_KEY', 'second-PRIVATE')
        other = manager(env={'SERVER_KEY': '${MCP_BOUND_KEY}'})
        async with resources.borrow('A', 'root', 'local') as lease:
            first = lease.config_identity
            assert records[0].environment == {'MCP_BOUND_KEY': 'first-PRIVATE'}
        async with other.borrow('A', 'root', 'local') as lease:
            assert lease.config_identity != first
            assert records[1].environment == {'MCP_BOUND_KEY': 'second-PRIVATE'}
        assert 'PRIVATE' not in first
        await resources.aclose()
        await other.aclose()
    asyncio.run(scenario())


def test_borrower_cannot_close_itself_or_enter_nested_borrow(monkeypatch):
    async def scenario():
        from naiyue_test_agent.mcp_client.resources import MCPResourceError
        fake_connections(monkeypatch)
        resources = manager()
        async with resources.borrow('A', 'root', 'local'):
            with pytest.raises(MCPResourceError, match='own_borrow'):
                await resources.aclose()
            with pytest.raises(MCPResourceError, match='own_borrow'):
                await resources.release_session('A')
            with pytest.raises(MCPResourceError, match='nested'):
                async with resources.borrow('A', 'root', 'local'):
                    pytest.fail('nested borrow would deadlock the slot')
        await resources.aclose()
    asyncio.run(scenario())


def test_owner_can_close_before_its_first_task_tick():
    async def scenario():
        from naiyue_test_agent.mcp_client.owner import ConnectionOwner
        from naiyue_test_agent.mcp_client.settings import MCPServerSettings
        from naiyue_test_agent.tools.web_settings import NetworkSettings
        owner = ConnectionOwner('local', MCPServerSettings(enabled=True, transport='stdio', command='must-not-run'),
                                NetworkSettings(), {}, 'identity')
        await owner.close()
        assert owner.task.done() and owner.ready.done()
    asyncio.run(scenario())


def test_cleanup_failure_in_one_session_does_not_fail_another_scope_release(monkeypatch):
    async def scenario():
        fake_connections(monkeypatch, exit_error=ValueError('PRIVATE cleanup'))
        resources = manager()
        with pytest.raises(OSError):
            async with resources.borrow('A', 'root', 'local'):
                raise OSError('submitted A')
        records = fake_connections(monkeypatch)
        async with resources.borrow('B', 'root', 'local'):
            pass
        await resources.release_session('B')
        assert records[0].closed.is_set()
        with pytest.raises(ExceptionGroup):
            await resources.aclose()
    asyncio.run(scenario())


def test_invalidated_connection_waits_for_other_existing_borrower(monkeypatch):
    async def scenario():
        from naiyue_test_agent.mcp_client.resources import MCPResourceError
        records = fake_connections(monkeypatch)
        resources = manager(max_concurrency=2)
        entered, finish = asyncio.Event(), asyncio.Event()
        async def remaining():
            async with resources.borrow('A', 'root', 'local'):
                entered.set()
                await finish.wait()
                assert not records[0].closed.is_set()
        task = asyncio.create_task(remaining())
        await entered.wait()
        with pytest.raises(OSError):
            async with resources.borrow('A', 'root', 'local'):
                raise OSError('submitted')
        assert not records[0].closed.is_set()
        with pytest.raises(MCPResourceError, match='connection_invalid'):
            async with resources.borrow('A', 'root', 'local'):
                pytest.fail('invalid connection accepted another borrower')
        finish.set()
        await task
        assert records[0].closed.is_set()
        await resources.aclose()
    asyncio.run(scenario())
