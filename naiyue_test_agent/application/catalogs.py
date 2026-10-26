"""Application access to current model, tool, role and MCP catalogs."""
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking


class ModelAPI:
    def __init__(self, owner, catalog):
        self._owner, self._catalog = owner, catalog

    @property
    def default_id(self):
        return self._catalog.default_model_id

    @property
    def ids(self):
        return self._catalog.model_ids

    def thinking_options(self):
        self._owner.check_open()
        return {identity: self._catalog.thinking_options(identity) for identity in self.ids}


class ToolAPI:
    def __init__(self, owner, sessions, registry):
        self._owner, self._sessions, self._tool_registry = owner, sessions, registry

    def list(self):
        self._owner.check_open()
        return self._tool_registry.select(self._tool_registry.names())

    async def register(self, spec):
        with self._owner.operation():
            self._sessions.register_tool(spec)


class SubagentAPI:
    def __init__(self, owner, sessions):
        self._owner, self._sessions = owner, sessions

    def list(self):
        self._owner.check_open()
        return self._sessions.get_registered_subagents()

    async def register(self, spec):
        with self._owner.operation():
            self._sessions.register_subagent(spec)


class McpAPI:
    def __init__(self, owner, sessions, registry):
        self._owner, self._sessions, self._tool_registry = owner, sessions, registry

    def status(self):
        self._owner.check_open()
        resources = self._sessions.mcp_resources
        return resources.server_status() if resources is not None else {'servers': []}

    async def refresh(self, settings_path=None):
        with self._owner.operation():
            from naiyue_test_agent.mcp_client.settings import load_mcp_settings
            from naiyue_test_agent.mcp_client.refresh import refresh_mcp
            settings = await run_blocking(load_mcp_settings, settings_path)
            return await refresh_mcp(self._sessions.mcp_resources, self._tool_registry, settings,
                borrow_mcp=self._sessions.borrow_mcp, permission_settings=self._sessions._permission_settings)

