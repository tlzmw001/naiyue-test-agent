"""Real SDK resources retain state and enforce wire budgets across leases."""

import asyncio
import logging

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.mcp_client.resources import MCPSessionResources, MCPResourceError
from naiyue_test_agent.mcp_client.settings import MCPSettings, MCPServerSettings
from naiyue_test_agent.tools.web_settings import NetworkSettings
from test_mcp_connections import facts, http_server, process_alive, stdio_settings, until


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('json_response', [False, True])
def test_http_reuses_client_and_closes_each_scope(tmp_path, monkeypatch, caplog, legacy, json_response):
    async def scenario():
        from naiyue_test_agent.mcp_client.connections import _BudgetTransport
        closed = []
        close = _BudgetTransport.aclose
        async def closing(transport):
            await close(transport)
            closed.append(transport)
        monkeypatch.setattr(_BudgetTransport, 'aclose', closing)
        audit = tmp_path / 'http-state.jsonl'
        async with http_server(audit, 'stateful', json_response, legacy=legacy) as url:
            resources = MCPSessionResources(MCPSettings(servers={'local': MCPServerSettings(
                enabled=True, transport='streamable_http', url=url)}), NetworkSettings(mode='direct'))
            clients, values = [], []
            try:
                for sid in ['A', 'A', 'B']:
                    async with resources.borrow(sid, 'root', 'local') as lease:
                        clients.append(lease.client)
                        result = await lease.client.session.call_tool('echo', {})
                        values.append(result.structured_content['count'])
                assert clients[0] is clients[1] and clients[0] is not clients[2]
                # This fixture's HTTP counter is server-global: isolated clients
                # intentionally do not claim server-side business-state isolation.
                assert values == [1, 2, 3] and closed == []
                await resources.release_session('A')
                assert len(closed) == 1
            finally:
                await resources.aclose()
            assert len(closed) == 2
            deletes = [row for row in facts(audit) if row['event'] == 'delete']
            assert len(deletes) == (2 if legacy else 0)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())
    assert '/mcp/' not in caplog.text and 'PRIVATE' not in caplog.text


@pytest.mark.parametrize('json_response', [False, True])
def test_http_catalog_read_budget_closes_owner_before_failed_borrow_returns(tmp_path, monkeypatch, json_response):
    async def scenario():
        from naiyue_test_agent.mcp_client.connections import _BudgetTransport
        closed = []
        original_close = _BudgetTransport.aclose
        async def closing(transport):
            await original_close(transport)
            closed.append(True)
        monkeypatch.setattr(_BudgetTransport, 'aclose', closing)
        audit = tmp_path / 'http-budget.jsonl'
        async with http_server(audit, 'large', json_response, legacy=True) as url:
            resources = MCPSessionResources(MCPSettings(servers={'local': MCPServerSettings(
                enabled=True, transport='streamable_http', url=url, max_response_bytes=4096)}), NetworkSettings(mode='direct'))
            with pytest.raises(MCPResourceError):
                async with resources.borrow('A', 'root', 'local'):
                    pytest.fail('oversized HTTP catalog must not be delivered')
            assert resources._owners == {} and resources._requests == {}
            # A fatal transport reader cancels the SDK task group: its best-effort
            # DELETE may itself be cancelled. The local transport must still close.
            assert closed == [True]
            assert any(row['event'] == 'list' for row in facts(audit))
            assert not any(row['event'] == 'call' for row in facts(audit))
            await resources.aclose()
    asyncio.run(scenario())


def test_real_stdio_cancellation_ends_process_without_replaying(tmp_path):
    from naiyue_test_agent.agent_runtime.run_control import RunControl
    async def scenario():
        audit = tmp_path / 'stop.jsonl'
        resources = MCPSessionResources(MCPSettings(servers={'local': stdio_settings(audit, 'stateful')}), NetworkSettings())
        control = RunControl()
        async def invoke():
            async with resources.borrow('A', 'root', 'local', control=control) as lease:
                await lease.client.session.call_tool('echo', {'text': 'wait'})
        task = asyncio.create_task(invoke())
        await until(lambda: any(row['event'] == 'call' for row in facts(audit)))
        control.request()
        with pytest.raises(ToolException, match='stopped'):
            await task
        audit_rows = facts(audit)
        assert len([r for r in audit_rows if r['event'] == 'call']) == 1
        assert not process_alive(audit_rows[0]['pid'])
        await resources.aclose()
    asyncio.run(scenario())


def test_response_loss_does_not_repeat_submitted_call(tmp_path):
    async def scenario():
        audit = tmp_path / 'drop.jsonl'
        resources = MCPSessionResources(MCPSettings(servers={'local': stdio_settings(audit, 'stateful')}), NetworkSettings())
        with pytest.raises(Exception):
            async with resources.borrow('A', 'root', 'local') as lease:
                await lease.client.session.call_tool('echo', {'text': 'drop'})
        assert len([r for r in facts(audit) if r['event'] == 'call']) == 1
        assert not process_alive(facts(audit)[0]['pid'])
        async with resources.borrow('A', 'root', 'local') as lease:
            result = await lease.client.session.call_tool('echo', {})
            assert result.structured_content['count'] == 1
        assert len([r for r in facts(audit) if r['event'] == 'call']) == 2
        await resources.aclose()
    asyncio.run(scenario())
