"""Real HTTP transports and runtime-added servers obey the same idle lifecycle."""

import asyncio

import pytest

from naiyue_test_agent.mcp_client.resources import MCPSessionResources
from naiyue_test_agent.mcp_client.settings import MCPServerSettings, MCPSettings
from naiyue_test_agent.tools.web_settings import NetworkSettings
from test_mcp_connections import facts, http_server, stdio_settings
from test_mcp_idle_reclamation import clock, expire, settled, use
from test_mcp_runtime_configuration import empty_app, write_config
from test_mcp_agent_tools import NAME, calls
from test_async_application import call
from langchain_core.messages import AIMessage


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('json_response', [False, True])
def test_http_idle_closes_transport_and_rebuilds_without_replaying(monkeypatch, tmp_path, legacy, json_response):
    from naiyue_test_agent.mcp_client.connections import _BudgetTransport
    async def scenario():
        closed = []
        original = _BudgetTransport.aclose
        async def closing(transport):
            await original(transport)
            closed.append(transport)
        monkeypatch.setattr(_BudgetTransport, 'aclose', closing)
        audit = tmp_path / 'http.jsonl'
        async with http_server(audit, 'stateful', json_response, legacy=legacy) as url:
            resources = MCPSessionResources(MCPSettings(servers={'local': MCPServerSettings(
                enabled=True, transport='streamable_http', url=url, idle_timeout_seconds=10)}),
                NetworkSettings(mode='direct'))
            now = clock(resources)
            try:
                async with resources.borrow('A', 'root', 'local') as lease:
                    first = lease.client
                    await lease.client.session.call_tool('echo', {})
                now[0] = 110
                expire(resources)
                await settled(resources)
                assert len(closed) == 1
                assert len([r for r in facts(audit) if r['event'] == 'call']) == 1
                assert len([r for r in facts(audit) if r['event'] == 'delete']) == int(legacy)
                async with resources.borrow('A', 'root', 'local') as lease:
                    assert lease.client is not first
                    await lease.client.session.call_tool('echo', {})
                assert len([r for r in facts(audit) if r['event'] == 'call']) == 2
            finally:
                await resources.aclose()
            assert len(closed) == 2 and not resources._idle_handles and not resources._idle_tasks
    asyncio.run(scenario())


def test_ttl_added_during_occupied_run_uses_existing_session_occupation(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'new.jsonl', tmp_path / 'settings.toml'
        app, model = await empty_app(monkeypatch, [AIMessage(content='first'), call(NAME, {}), AIMessage(content='done')])
        entered, release = asyncio.Event(), asyncio.Event()
        model.entered, model.release = entered, release
        sid = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(sid, [('user', 'hold model')]))
        resources = app._sessions.mcp_resources
        now = clock(resources)
        try:
            await entered.wait()
            write_config(config, {'local': stdio_settings(path, 'stateful', allowed_tools=['echo'], idle_timeout_seconds=10)})
            await app.arefresh_mcp(config)
            client = await use(resources, sid)
            assert sid in resources._active_sessions and not resources._idle_handles
            now[0] = 1000
            expire(resources)
            release.set()
            await task
            assert next(iter(resources._idle_handles.values()))[2] == 1010
            now[0] = 1010
            expire(resources)
            await settled(resources)
            assert not resources._owners and not calls(path)
            await app.aset_active_tools(sid, [NAME])
            await app.arun(sid, [('user', 'call new')])
            await app.aresume_tool_approval(sid, ['approve'])
            assert len(calls(path)) == 1
            assert next(iter(resources._owners.values())).ready.result().client is not client
        finally:
            release.set()
            await app.aclose()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
