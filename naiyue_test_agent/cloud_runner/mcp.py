"""Task-local cloud MCP catalog, with an explicit route for each service."""
from naiyue_test_agent.contracts.cloud_mcp import tunnel_target
from naiyue_test_agent.mcp_client.refresh import refresh_mcp
from naiyue_test_agent.mcp_client.resources import MCPSessionResources
from naiyue_test_agent.mcp_client.settings import MCPSettings
from naiyue_test_agent.tools.web_settings import NetworkSettings


class RunnerMCP:
    def __init__(self, registry, permissions):
        self.resources = MCPSessionResources(MCPSettings(), NetworkSettings(mode='direct'), select_all=True)
        self.registry, self.permissions = registry, permissions
        self.sessions = None
        self._tools = {}
        self.run_tools = None
        self.applied_revision = None
        self.applied_digest = None
        self.report = []

    def names(self):
        return self.run_tools or ()

    def names_for(self, services=None):
        return tuple(name for server, dial, names in self._tools.values()
            if server.route == 'direct' or dial is not None and tunnel_target(server, services) == dial
            for name in names)

    async def pin_run(self, revision, services):
        self.run_tools = ()
        if revision is None or revision != self.applied_revision:
            return
        for name, (server, dial, names) in tuple(self._tools.items()):
            if names and server.route == 'windows_tunnel' and tunnel_target(server, services) != dial:
                await self.resources.disable_server(name)
                self._tools[name] = (server, None, [])
                self.applied_digest = None
        self.run_tools = self.names_for(services)

    async def apply(self, settings, services, *, dial_deadline=None):
        definitions, dials, unroutable = {}, {}, set()
        for name, server in settings.servers.items():
            definition = server.client_settings()
            if server.enabled and server.route == 'windows_tunnel':
                dial = tunnel_target(server, services)
                if dial is None:
                    definition = definition.model_copy(update={'enabled': False})
                    unroutable.add(name)
                else:
                    dials[name] = dial
            definitions[name] = definition
        status = await refresh_mcp(self.resources, self.registry, MCPSettings(servers=definitions),
            borrow_mcp=self.sessions.borrow_mcp, permission_settings=self.permissions, dial_targets=dials, dial_deadline=dial_deadline)
        effective = {item['server_id']: item for item in status['servers']}
        self._tools = {}
        report = []
        for name, server in settings.servers.items():
            item = effective.get(name, {})
            names = self.resources.effective_tool_names(name)
            self._tools[name] = (server, dials.get(name), names)
            error = 'mcp_tunnel_target_unavailable' if name in unroutable else item.get('error')
            report.append(dict(server_id=name, route=server.route, tools=names,
                status='error' if error else 'available' if server.enabled else 'disabled', error=error))
        return report
