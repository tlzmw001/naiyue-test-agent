"""Graph control and cancellation cross retry waits without ordinary feedback."""

import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import pytest
from langgraph.errors import GraphBubbleUp, GraphInterrupt

from naiyue_test_agent.mcp_client.discovery import DiscoveredCatalog, DiscoveredTool
from naiyue_test_agent.mcp_client.settings import MCPServerSettings
from naiyue_test_agent.mcp_client.tools import create_mcp_tool
from naiyue_test_agent.tools.permissions import execution_environment
from naiyue_test_agent.tools.retry_settings import ToolRetrySettings


@pytest.mark.parametrize('stage', ['preflight', 'retry_wait'])
@pytest.mark.parametrize('fault', [GraphBubbleUp('control'), GraphInterrupt(())])
def test_graph_control_is_not_converted_or_retried(monkeypatch, stage, fault):
    from naiyue_test_agent.mcp_client import tools
    async def scenario():
        attempts = []
        tool = DiscoveredTool('echo', 'mcp__local__echo', True,
            json.dumps({'inputSchema': {'type': 'object'}}))
        catalog = DiscoveredCatalog('local', (tool,), 'catalog')
        @asynccontextmanager
        async def borrow(*_):
            attempts.append(1)
            raise fault if stage == 'preflight' else TimeoutError()
            yield
        class Progress:
            def __init__(self, *_): pass
            def report(self, **_): raise fault
        monkeypatch.setattr(tools, 'ToolProgress', Progress)
        resources = SimpleNamespace(retry_settings=ToolRetrySettings(initial_delay_seconds=0),
                                    tool_unavailable=lambda *_: None)
        spec = create_mcp_tool(borrow, MCPServerSettings(transport='stdio', command='synthetic'),
                              'config', catalog, tool, resources=resources)
        subject = {'operation': 'mcp_call', 'mcp': spec.permissions.identity(),
                   'args': {}, 'execution_environment': execution_environment()}
        runtime = SimpleNamespace(context={'session_id': 's', '_execution_permit': {
            'session_id': 's', 'agent_path': 'root', 'call_id': 'c', 'tool_name': tool.registered_name,
            'subject': subject, 'args': {}}}, tool_call_id='c')
        with pytest.raises(type(fault)) as caught:
            await spec.tool.coroutine(runtime)
        assert caught.value is fault and attempts == [1]
    asyncio.run(scenario())


@pytest.mark.parametrize('phase', ['resource_wait', 'rpc'])
@pytest.mark.parametrize('deadline', ['total', 'attempt'])
def test_deadline_includes_resource_wait_and_settles_before_return(monkeypatch, phase, deadline):
    from naiyue_test_agent.mcp_client import tools
    async def scenario():
        settled, calls, borrows = asyncio.Event(), [], []
        tool = DiscoveredTool('echo', 'mcp__local__echo', True,
            json.dumps({'inputSchema': {'type': 'object'}}))
        catalog = DiscoveredCatalog('local', (tool,), 'catalog')
        async def read(*_): return catalog
        async def call(*_, **kwargs):
            calls.append(1)
            await asyncio.Event().wait()
        monkeypatch.setattr(tools, 'read_catalog', read)
        @asynccontextmanager
        async def borrow(*_):
            borrows.append(1)
            try:
                if phase == 'resource_wait':
                    await asyncio.Event().wait()
                yield SimpleNamespace(config_identity='config', catalog=catalog,
                    client=SimpleNamespace(session=SimpleNamespace(call_tool=call)))
            finally:
                settled.set()
        resources = SimpleNamespace(retry_settings=ToolRetrySettings(max_retries=0,
            attempt_timeout_seconds=.01 if deadline == 'attempt' else 30), tool_unavailable=lambda *_: None)
        spec = create_mcp_tool(borrow, MCPServerSettings(transport='stdio', command='synthetic',
            call_timeout_seconds=.01 if deadline == 'total' else 0),
            'config', catalog, tool, resources=resources)
        subject = {'operation': 'mcp_call', 'mcp': spec.permissions.identity(),
                   'args': {}, 'execution_environment': execution_environment()}
        runtime = SimpleNamespace(context={'session_id': 's', '_execution_permit': {
            'session_id': 's', 'agent_path': 'root', 'call_id': 'c', 'tool_name': tool.registered_name,
            'subject': subject, 'args': {}}}, tool_call_id='c')
        result = await spec.tool.coroutine(runtime)
        assert result.status == 'error' and 'timed out' in result.content
        assert result.artifact['outcome'] == ('not_executed' if phase == 'resource_wait' else 'outcome_unknown')
        assert settled.is_set() and len(borrows) == 1 and len(calls) == int(phase == 'rpc')
    asyncio.run(scenario())
