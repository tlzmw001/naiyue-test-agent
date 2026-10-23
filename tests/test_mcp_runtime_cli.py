"""Terminal commands target the running application, not a second process."""

from types import SimpleNamespace

from naiyue_test_agent.mcp_client.tools import MCPPermission


def test_runtime_cli_refresh_and_incremental_selection():
    from naiyue_test_agent.cli.cli_mcp import handle_mcp_command
    selected = ['read']; lines = []; refreshed = []
    specs = [SimpleNamespace(tool=SimpleNamespace(name=name), permissions=MCPPermission() if name.startswith('mcp') else None)
             for name in ('read', 'mcp__local__echo')]
    app = SimpleNamespace(refresh_mcp=lambda: refreshed.append(True) or {'servers': [{'server_id': 'local',
        'status': 'enabled', 'enabled': True, 'selected_tools': ['echo']}]},
        get_registered_tools=lambda: specs, get_active_tools=lambda sid: tuple(selected),
        set_active_tools=lambda sid, names: selected.__setitem__(slice(None), names))
    handle_mcp_command(app, 's', ':mcp-refresh', lines.append)
    handle_mcp_command(app, 's', ':tools +mcp__local__echo -read', lines.append)
    assert refreshed == [True] and selected == ['mcp__local__echo']
    handle_mcp_command(app, 's', ':tools', lines.append)
    assert any('mcp__local__echo' in row and '已选用' in row for row in lines)


def test_cli_disabled_tool_cannot_be_newly_enabled():
    from naiyue_test_agent.cli.cli_mcp import handle_mcp_command
    class Disabled(MCPPermission):
        def unavailable_reason(self): return 'server_disabled'
    lines = []; selected = []
    spec = SimpleNamespace(tool=SimpleNamespace(name='mcp__local__echo'), permissions=Disabled())
    app = SimpleNamespace(get_registered_tools=lambda: [spec], get_active_tools=lambda sid: (),
                          set_active_tools=lambda sid, names: selected.extend(names))
    handle_mcp_command(app, 's', ':tools +mcp__local__echo', lines.append)
    assert selected == [] and any('不可用' in row for row in lines)
