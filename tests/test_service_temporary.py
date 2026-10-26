"""A temporary scope is owned by one network lease, never by its run stream."""
import asyncio
from uuid import uuid4
import httpx
from naiyue_test_agent.service.client import ServiceClient
from test_async_application import StreamingToolModel
from test_service_tcp import tcp_service


def test_lease_disconnect_stops_run_drops_ram_and_rejects_cached_commands(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[], entered=entered, release=release)
        async with tcp_service(monkeypatch, tmp_path, model) as (app, config, _, _):
            client = await ServiceClient.connect(config)
            try:
                ticket = await client.request('POST', '/api/v1/temporary', json={'root': str(tmp_path)})
                identity = ticket['scope_id']
                base = f'/api/v1/scopes/{identity}'
                async with client._http.stream('GET', f'/api/v1/temporary/{identity}/lease', timeout=None) as lease:
                    assert lease.status_code == 200
                    lines = lease.aiter_lines()
                    while not (await anext(lines)).startswith('data:'):
                        pass
                    key = str(uuid4())
                    sid = (await client.request('POST', base + '/sessions', json={'model_id': 'A'}, request_id=key))['session_id']
                    host = app.state.scopes.services[identity].application
                    await client.request('POST', f'{base}/sessions/{sid}/runs', json={'text': 'private question', 'request_id': str(uuid4())})
                    await asyncio.wait_for(entered.wait(), 5)
                await asyncio.wait_for(app.state.temporary.tickets[identity].closed.wait(), 5)
                assert host._lifetime._closed
                assert host._lifetime not in app.state.application._lifetime._children
                assert identity not in app.state.scopes.services
                assert key not in app.state.receipts.entries
                assert not release.is_set()
                response = await client._http.post(base + '/sessions', json={'model_id': 'A'}, headers={'X-Request-ID': key})
                assert response.status_code == 409
                assert response.json()['error']['code'] == 'temporary_scope_closed'
                assert (await client._http.get(base + f'/sessions/{sid}')).status_code == 409
            finally:
                await client.aclose()
    asyncio.run(scenario())


def test_cleanup_failure_still_purges_delivery_output_plan_and_sensitive_task(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from naiyue_test_agent.service.temporary import TemporaryScopes, Ticket
    async def scenario():
        service = SimpleNamespace(delivery=SimpleNamespace(seal=AsyncMock(), close=AsyncMock()))
        scopes = SimpleNamespace(cancel_open=AsyncMock(), services={'temp-fault': service},
            output_plans={'confirm': ('temp-fault', object())},
            application=SimpleNamespace(temporary=SimpleNamespace(close=AsyncMock(side_effect=OSError('private')))))
        receipts = SimpleNamespace(forget_scope=AsyncMock())
        manager = TemporaryScopes(scopes, receipts)
        manager.tickets['temp-fault'] = Ticket(None, state='active')
        import pytest
        with pytest.raises(OSError):
            await manager.close('temp-fault')
        service.delivery.close.assert_awaited_once_with(close_application=False)
        assert scopes.services == {} and scopes.output_plans == {}
        await asyncio.sleep(0)  # deliver a completion callback, not a timing threshold
        assert manager.tickets['temp-fault'].close_task is None
    asyncio.run(scenario())


def test_temporary_host_close_does_not_hold_another_scopes_gate():
    from types import SimpleNamespace
    from naiyue_test_agent.application.projects.host_manager import HostManager
    from naiyue_test_agent.application.lifetime import ScopeTasks
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        lifetime = SimpleNamespace(_closed=False)
        async def close():
            entered.set()
            await release.wait()
            lifetime._closed = True
        lifetime.aclose = close
        manager = object.__new__(HostManager)
        manager._scopes = ScopeTasks()
        manager._temporary = {'temp-a': SimpleNamespace(_lifetime=lifetime)}
        manager.owner = SimpleNamespace(_children=[lifetime])
        task = asyncio.create_task(manager.close_temporary('temp-a'))
        try:
            await entered.wait()
            async with asyncio.timeout(2):
                async with manager._scopes.serial(('temporary', 'temp-b')):
                    assert not task.done(), 'Other terminal startup must not wait for temporary teardown'
        finally:
            release.set()
            await task
        assert manager.owner._children == [] and manager._temporary == {}
    asyncio.run(scenario())


def test_root_cleanup_attempts_every_ram_stack_and_drops_references():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from naiyue_test_agent.application.projects.host_manager import HostManager
    import pytest
    async def scenario():
        first = SimpleNamespace(_resources=SimpleNamespace(aclose=AsyncMock(side_effect=OSError('first'))))
        second = SimpleNamespace(_resources=SimpleNamespace(aclose=AsyncMock()))
        manager = object.__new__(HostManager)
        manager.owner = SimpleNamespace(_children=[first, second])
        manager._temporary = {'temp-a': SimpleNamespace(_lifetime=first), 'temp-b': SimpleNamespace(_lifetime=second)}
        with pytest.raises(OSError, match='first'):
            await manager.close_temporary_resources()
        first._resources.aclose.assert_awaited_once()
        second._resources.aclose.assert_awaited_once()
        assert manager._temporary == {} and manager.owner._children == []
    asyncio.run(scenario())


def test_shutdown_cancels_a_lease_waiting_for_host_assembly(monkeypatch, tmp_path):
    from naiyue_test_agent.application.projects import host_manager
    async def scenario():
        model = StreamingToolModel(replies=[])
        async with tcp_service(monkeypatch, tmp_path, model) as (app, config, _, serving):
            entered, cancelled = asyncio.Event(), asyncio.Event()
            original = host_manager.assemble_host
            async def waiting(**options):
                if options['database_path'] is None:
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                return await original(**options)
            monkeypatch.setattr(host_manager, 'assemble_host', waiting)
            client = await ServiceClient.connect(config)
            ticket = await client.request('POST', '/api/v1/temporary', json={'root': str(tmp_path)})
            response = client._http.stream('GET', f'/api/v1/temporary/{ticket["scope_id"]}/lease', timeout=None)
            try:
                async with response as lease:
                    assert lease.status_code == 200
                    await asyncio.wait_for(entered.wait(), 5)
                    await client.request('POST', '/api/v1/shutdown')
                    await asyncio.wait_for(cancelled.wait(), 5)
                    await asyncio.wait_for(asyncio.shield(serving), 5)
                    assert app.state.temporary.tickets[ticket['scope_id']].closed.is_set()
                    assert ticket['scope_id'] not in app.state.scopes.services
            finally:
                await client.aclose()
    asyncio.run(scenario())


def test_lease_send_error_preserves_primary_and_explicitly_closes_iterator():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from naiyue_test_agent.service.temporary import LeaseResponse, Ticket
    import pytest
    async def scenario():
        # Starlette translates OSError into ClientDisconnect; use an unchanged
        # send failure to assert the request boundary preserves its primary.
        primary = RuntimeError('send')
        manager = SimpleNamespace(scopes=SimpleNamespace(get=AsyncMock()),
            close=AsyncMock(side_effect=ValueError('cleanup')))
        response = LeaseResponse(manager, 'temp-a', Ticket(None))
        async def send(_): raise primary
        async def receive(): await asyncio.Event().wait()
        with pytest.raises(RuntimeError) as caught:
            await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
        assert caught.value is primary
        manager.close.assert_awaited_once_with('temp-a')
        assert response.body_iterator.ag_frame is None
    asyncio.run(scenario())


def test_repeated_leases_stop_background_processes_without_affecting_persistent_client(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage
    from test_background_tasks import submit, wait_status
    import os
    import pytest
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='persistent still works')])
        async with tcp_service(monkeypatch, tmp_path, model) as (app, config, _, _):
            client = await ServiceClient.connect(config)
            try:
                persistent = '/api/v1/scopes/chats'
                sid = (await client.request('POST', persistent + '/sessions', json={'model_id': 'A'}))['session_id']
                owner = app.state.application._lifetime
                baseline = len(owner._children), len(owner._resources._exit_callbacks)
                for _ in range(2):
                    ticket = await client.request('POST', '/api/v1/temporary', json={'root': str(tmp_path)})
                    identity = ticket['scope_id']
                    base = f'/api/v1/scopes/{identity}'
                    async with client._http.stream('GET', f'/api/v1/temporary/{identity}/lease', timeout=None) as lease:
                        lines = lease.aiter_lines()
                        while not (await anext(lines)).startswith('data:'):
                            pass
                        tsid = (await client.request('POST', base + '/sessions', json={'model_id': 'A'}))['session_id']
                        manager = app.state.scopes.services[identity].application.sessions._sessions.background_tasks
                        row = await submit(manager, tmp_path, 'import os,time;print(os.getpid(),flush=True);time.sleep(60)', sid=tsid)
                        ready = await wait_status(manager, row['task_id'], lambda value: bool(value['output']))
                        pid = int(ready['output'])
                    await asyncio.wait_for(app.state.temporary.tickets[identity].closed.wait(), 5)
                    assert (await manager.output(row['task_id']))['cleanup_confirmed']
                    with pytest.raises(ProcessLookupError): os.kill(pid, 0)
                    assert (len(owner._children), len(owner._resources._exit_callbacks)) == baseline
                    assert identity not in app.state.scopes.services
                run = await client.request('POST', persistent + f'/sessions/{sid}/runs',
                    json={'request_id': str(uuid4()), 'text': 'still connected'})
                service = app.state.scopes.services['chats']
                await service.delivery.wait(run['run_id'])
                assert (await client.request('GET', persistent + f'/runs/{run["run_id"]}'))['status'] == 'completed'
            finally:
                await client.aclose()
    asyncio.run(scenario())
