"""Sandbox stdio policy filters views and blocks forced execution before borrow."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.contracts.run_context import RunIdentity, RunScope, RuntimeContext, ToolAuthorization
from naiyue_test_agent.mcp_client.discovery import DiscoveredTool, DiscoveredCatalog
from naiyue_test_agent.mcp_client.settings import MCPServerSettings
from naiyue_test_agent.mcp_client.tools import create_mcp_tool
from naiyue_test_agent.tools.permissions import execution_environment
from naiyue_test_agent.tools.registry import ToolRegistry


def test_default_stdio_is_hidden_and_forced_call_never_borrows():
    async def scenario():
        borrowed = []
        def borrow(*args):
            borrowed.append(args)
            raise AssertionError('Forbidden stdio must not be borrowed')
        server = MCPServerSettings(transport='stdio', command='unused')
        tool = DiscoveredTool('echo', 'mcp__local__echo', True,
            json.dumps({'inputSchema': {'type': 'object', 'properties': {}}}))
        catalog = DiscoveredCatalog('local', (tool,), 'fingerprint')
        spec = create_mcp_tool(borrow, server, 'config', catalog, tool)
        registry = ToolRegistry(); registry.register(spec)
        context = RuntimeContext(RunScope(RunIdentity('S'),
            environment=EnvironmentRef('sandbox:one', 'sandbox', None, '/project', 'linux')))
        assert registry.select_available(registry.names(), context=context) == ()
        subject = spec.permissions.prepare({}, context)
        subject.update(args={}, execution_environment=execution_environment(context))
        permit = dict(session_id='S', agent_path='root', call_id='one', tool_name=tool.registered_name,
                      args={}, subject=subject)
        runtime = SimpleNamespace(tool_call_id='one', context=replace(context, authorization=ToolAuthorization(permit=permit)))
        result = await spec.tool.coroutine(runtime=runtime)
        assert result.status == 'error' and result.artifact['outcome'] == 'not_executed'
        from naiyue_test_agent.agent_runtime.tool_execution.toolset import ToolSetMiddleware
        middleware = ToolSetMiddleware(registry, lambda *_: registry.names(), '')
        request = SimpleNamespace(runtime=runtime, tool_call={'name': tool.registered_name, 'args': {}, 'id': 'one'},
            state={'tool_execution': {'tool_names': [tool.registered_name]}})
        request.override = lambda **_: request
        dispatched = []
        async def handler(_):
            dispatched.append(True)
            raise AssertionError('Forbidden stdio must not reach dispatch')
        guarded = await middleware.awrap_tool_call(request, handler)
        assert guarded.status == 'error'
        assert guarded.additional_kwargs['naiyue_not_executed'] == 'stdio_not_allowed_for_environment'
        assert borrowed == [] and dispatched == []
    asyncio.run(scenario())


@pytest.mark.parametrize('transport,opt_in,kind,visible', [
    ('stdio', False, 'host', True), ('stdio', False, 'sandbox', False),
    ('stdio', True, 'sandbox', True), ('streamable_http', False, 'sandbox', True)])
def test_visibility_respects_transport_and_explicit_opt_in(transport, opt_in, kind, visible):
    from naiyue_test_agent.mcp_client.tools import MCPPermission
    server = MCPServerSettings(transport=transport, allow_sandbox=opt_in,
        **({'command': 'unused'} if transport == 'stdio' else {'url': 'https://example.invalid/mcp'}))
    context = RuntimeContext(RunScope(RunIdentity('S'),
        environment=EnvironmentRef('binding', kind, None, '/project', 'linux')))
    permission = MCPPermission(transport=transport, allow_sandbox=server.allow_sandbox)
    assert (permission.unavailable_reason(context) is None) == visible
