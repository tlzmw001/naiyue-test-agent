"""Retries keep the original permission and never infer remote replay safety."""

import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from mcp.types import CallToolResult, TextContent

from naiyue_test_agent.mcp_client.discovery import DiscoveredCatalog, DiscoveredTool
from naiyue_test_agent.mcp_client.settings import MCPServerSettings
from naiyue_test_agent.mcp_client.tools import create_mcp_tool
from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
from tool_feedback_helpers import invoke_tool_feedback


NAME = 'mcp__local__echo'


@pytest.mark.parametrize('case,expected,status,outcome', [
    ('preflight', 1, 'success', 'success'),
    ('unknown', 1, 'error', 'outcome_unknown'),
    ('safe', 2, 'success', 'success'),
    ('safe_then_preflight', 1, 'error', 'outcome_unknown'),
    ('safe_then_disabled', 1, 'error', 'outcome_unknown'),
    ('business', 1, 'error', 'tool_error'),
    ('permanent', 0, 'error', 'not_executed'),
])
def test_mcp_retries_only_typed_failures_with_explicit_replay_safety(monkeypatch, case, expected, status, outcome):
    from naiyue_test_agent.mcp_client import tools
    async def scenario():
        counts = {'borrow': 0, 'catalog': 0, 'call': 0, 'settled': 0}
        safe = (NAME,) if case.startswith('safe') else ()
        policy = ToolRetrySettings(initial_delay_seconds=0, replay_safe_tools=safe)
        definition = json.dumps({'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}}})
        tool = DiscoveredTool('echo', NAME, True, definition)
        catalog = DiscoveredCatalog('local', (tool,), 'catalog')
        async def read_catalog(*_):
            counts['catalog'] += 1
            if case == 'preflight' and counts['catalog'] == 1:
                raise httpx.ReadError('PRIVATE')
            return catalog
        monkeypatch.setattr(tools, 'read_catalog', read_catalog)
        async def call_tool(name, arguments, **kwargs):
            counts['call'] += 1
            assert name == 'echo' and arguments == {'text': 'original'}
            if case == 'unknown' or (case.startswith('safe') and counts['call'] == 1):
                arguments['text'] = 'remote mutation must not change next attempt'
                raise httpx.ReadError('PRIVATE')
            return CallToolResult(content=[TextContent(type='text', text='ok')], is_error=case == 'business')
        @asynccontextmanager
        async def borrow(*_):
            counts['borrow'] += 1
            try:
                if case == 'permanent':
                    raise ValueError('PRIVATE')
                if case == 'safe_then_preflight' and counts['borrow'] > 1:
                    raise httpx.ConnectError('PRIVATE')
                yield SimpleNamespace(config_identity='config', catalog=catalog,
                    client=SimpleNamespace(session=SimpleNamespace(call_tool=call_tool)))
            finally:
                counts['settled'] += 1
        resources = SimpleNamespace(retry_settings=policy, tool_unavailable=lambda *_:
            'disabled' if case == 'safe_then_disabled' and counts['call'] else None)
        spec = create_mcp_tool(borrow, MCPServerSettings(transport='stdio', command='synthetic'),
            'config', catalog, tool, resources=resources)
        result = await invoke_tool_feedback(spec, {'text': 'original'}, call_id='mcp-retry',
                                            permission_mode='BypassApprovals')
        assert result.status == status and result.artifact['outcome'] == outcome
        assert counts['call'] == expected and counts['settled'] == counts['borrow']
        assert counts['borrow'] == (2 if case in ('preflight', 'safe') else 3 if case == 'safe_then_preflight' else 1)
        assert 'PRIVATE' not in result.content
        if case == 'safe_then_disabled':
            assert 'may have occurred' in result.content and 'No tools/call was sent' not in result.content
    asyncio.run(scenario())


@pytest.mark.parametrize('error,temporary', [(TimeoutError(), True), (httpx.ConnectError('PRIVATE'), True),
    (httpx.ProxyError('PRIVATE'), False), (ValueError('PRIVATE'), False)])
def test_connection_owner_preserves_only_trusted_temporary_classification(monkeypatch, error, temporary):
    from naiyue_test_agent.mcp_client import connections
    from naiyue_test_agent.mcp_client.owner import MCPResourceError
    from test_mcp_lifecycle import manager
    @asynccontextmanager
    async def connect(*_, **kwargs):
        raise error
        yield
    monkeypatch.setattr(connections, 'open_connection', connect)
    async def scenario():
        resources = manager()
        try:
            with pytest.raises(MCPResourceError) as caught:
                async with resources.borrow('A', 'root', 'local'):
                    pytest.fail('failed connection must not yield')
            assert caught.value.retryable is temporary and 'PRIVATE' not in str(caught.value)
            assert not resources._owners and not resources._requests
        finally:
            await resources.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('policy', ['compression', 'budget'])
def test_mcp_transport_response_policy_failure_is_permanent(monkeypatch, policy):
    import httpx2
    from naiyue_test_agent.mcp_client import connections
    from naiyue_test_agent.mcp_client.owner import MCPResourceError
    from test_mcp_lifecycle import manager
    async def scenario():
        observed = []
        transport = connections._BudgetTransport(None, 1)
        await transport.inner.aclose()
        class Body(httpx2.AsyncByteStream):
            async def __aiter__(self):
                yield b'PRIVATE'
        headers = {'content-encoding': 'gzip'} if policy == 'compression' else {'content-type': 'application/json'}
        transport.inner = httpx2.MockTransport(lambda request: httpx2.Response(200,
            headers=headers, stream=Body()))
        async def limited():
            async with httpx2.AsyncClient(transport=transport) as client:
                await client.get('https://server.invalid')
        @asynccontextmanager
        async def connect(*_, **kwargs):
            try:
                await limited()
            except Exception as error:
                observed.append(error)
                raise
            yield
        monkeypatch.setattr(connections, 'open_connection', connect)
        resources = manager()
        try:
            with pytest.raises(MCPResourceError) as caught:
                async with resources.borrow('A', 'root', 'local'):
                    pytest.fail('rejected response must not yield')
            assert len(observed) == 1 and isinstance(observed[0], httpx2.ReadError)
            assert caught.value.retryable is False
        finally:
            await resources.aclose()
    asyncio.run(scenario())



def test_real_sdk_preflight_retry_uses_one_approved_call_and_one_business_request(monkeypatch, tmp_path):
    from naiyue_test_agent import application
    from scripts.component_fixtures import build_component_application
    from naiyue_test_agent.mcp_client import tools
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from test_async_application import StreamingToolModel, call
    from test_mcp_connections import stdio_settings
    from test_mcp_agent_tools import calls
    async def scenario():
        path = tmp_path / 'retry.jsonl'
        model = StreamingToolModel(replies=[call(NAME, {'text': 'hello'}), AIMessage(content='done')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        settings = AppSettings.model_validate({'mcp': {'servers': {'local':
            stdio_settings(path, 'stateful', allowed_tools=['echo']).model_dump()}},
            'tool_retry': {'initial_delay_seconds': 0}})
        app = await build_component_application('unused', tools=[], system_prompt='', response_format=None,
            initial_active_tools=[NAME], settings=settings)
        events = []
        app.host.observability.subscribe(events.append)
        original = tools.read_catalog
        attempts = []
        async def read(*args):
            attempts.append(1)
            if len(attempts) == 1:
                raise TimeoutError()
            return await original(*args)
        monkeypatch.setattr(tools, 'read_catalog', read)
        try:
            sid = await app.host.sessions.create('A')
            waiting = await app.host.runs.start(sid, [('user', 'run')])
            assert waiting['approval_requests'] and not attempts and not calls(path)
            result = await app.host.runs.resume_approval(sid, ['approve'])
            output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert output.status == 'success' and len(attempts) == 2 and len(calls(path)) == 1
            assert app.host.sessions._sessions.mcp_resources.retry_settings.initial_delay_seconds == 0
            assert not result.get('approval_requests')
            for event_type in ('tool_call_started', 'tool_call_finished'):
                selected = [event for event in events if event.event_type == event_type]
                assert len(selected) == 1 and selected[0].data.tool_call_id == 't'
            progress = [event for event in events if event.event_type == 'tool_progress'
                        and event.data.phase == 'retry_wait']
            assert progress and all(event.data.tool_call_id == 't' for event in progress)
        finally:
            await app.aclose()
    asyncio.run(scenario())
