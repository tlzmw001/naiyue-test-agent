"""Missing legacy names cannot select a different MCP service by prefix."""
from types import SimpleNamespace

import pytest

from naiyue_test_agent.cli.cli_mcp import handle_mcp_command
from naiyue_test_agent.mcp_client.tools import MCPPermission
from naiyue_test_agent.tools import ToolRegistry, ToolSpec
from test_tool_registry import ProbeInput, probe_tool


@pytest.mark.parametrize('old_name', ['mcp__a__b__echo', 'mcp__a__echo__detail', 'mcp__a___echo'])
def test_ambiguous_missing_name_stays_saved_but_does_not_select_another_service(old_name):
    registry = ToolRegistry()
    registry.register(ToolSpec(tool=probe_tool('mcp__a__other'), input_schema=ProbeInput,
                               permissions=MCPPermission(server_id='a', remote_tool='other')))
    assert registry.session_selection([old_name]) == (old_name,)
    assert registry.expand_session_tools([old_name]) == ()
    assert registry.select_available([old_name]) == ()
    assert registry.expand_session_tools(['mcp__a__old']) == ('mcp__a__other',)
    assert registry.expand_session_tools(['mcp:a']) == ('mcp__a__other',)


def test_registered_ambiguous_name_uses_its_recorded_service_identity():
    registry = ToolRegistry()
    for name, server in [('mcp__a__b__echo', 'a__b'), ('mcp__a__other', 'a')]:
        registry.register(ToolSpec(tool=probe_tool(name), input_schema=ProbeInput,
                                   permissions=MCPPermission(server_id=server, remote_tool='echo')))
    assert registry.expand_session_tools(['mcp__a__b__echo']) == ('mcp__a__b__echo',)


@pytest.mark.parametrize('old', ['mcp__a__b__echo', 'mcp__a___echo'])
def test_terminal_does_not_show_or_replace_ambiguous_selection_as_current_service(old):
    selected = [old]
    lines = []
    def request(method, path, **kwargs):
        if path == '/session-controls':
            return {'tools': [{'name': 'mcp__a__other', 'mcp_source': {'server_id': 'a'}}],
                    'mcp_servers': [{'server_id': 'a', 'enabled': True}]}
        if method == 'GET':
            return {'active_tools': selected.copy()}
        selected[:] = kwargs['json']['active_tools']
    app = SimpleNamespace(request=request)
    handle_mcp_command(app, 's', ':tools', lines.append)
    assert 'mcp:a：未选用' in lines
    handle_mcp_command(app, 's', ':tools +mcp:a', lines.append)
    assert selected == [old, 'mcp:a']


def test_explicit_service_group_can_be_the_initial_selection(monkeypatch, tmp_path):
    import asyncio
    from test_async_application import call
    from test_mcp_agent_tools import NAME
    from test_mcp_recovery import opened
    async def scenario():
        async with opened(monkeypatch, tmp_path / 'audit.jsonl', tmp_path / 'state.sqlite',
                          [call(NAME, {}, 'new')], initial=('mcp:local',)) as (app, model):
            sid = await app.host.sessions.create('A')
            assert app.host.sessions.active_tools(sid) == ('mcp:local',)
            result = await app.host.runs.start(sid, [('user', 'new request')])
            assert result['approval_requests'][0]['tool_name'] == NAME
    asyncio.run(scenario())
