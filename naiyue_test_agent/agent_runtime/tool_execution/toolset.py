"""One request's original definitions, shared by prompt, budget and execution."""

from typing import NotRequired
from copy import deepcopy

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage

from .observation import aobserve_tool_dispatch
from ..context_usage import compose_model_prompt
from naiyue_test_agent.tools.subagent import subagent_request_tools


class ToolExecutionState(AgentState):
    tool_execution: NotRequired[dict]


def batch_tool_specs(registry, state, context):
    names = state.get('tool_execution', {}).get('tool_names')
    if names is None:
        raise RuntimeError('Current tool selection is unavailable')
    view = (context.invocation.model_call.tool_view if context.invocation.model_call is not None else None)
    if view is not None and tuple(spec.tool.name for spec in view) == tuple(names):
        return view
    return registry.select(names)


class ToolSetMiddleware(AgentMiddleware):
    state_schema = ToolExecutionState

    def __init__(self, registry, resolve_active_tool_names, system_prompt, *, resolve_subagent_roles=None, resolve_prompt_parts=None):
        self.registry = registry
        self.resolve_active_tool_names = resolve_active_tool_names
        self.system_prompt = system_prompt
        self.resolve_subagent_roles = resolve_subagent_roles
        self.resolve_prompt_parts = resolve_prompt_parts

    def prepare_tool_view(self, runtime, *, names=None):
        context = runtime.context
        if names is None:
            names = self.resolve_active_tool_names(context.scope.identity.session_id, context.scope.identity.agent_path)
        names = subagent_request_tools(names, self.registry, agent_path=context.scope.identity.agent_path)
        view = self.registry.select_available(names)
        context.invocation.model_call.tool_view = view
        context.invocation.model_call.subagent_roles = (tuple((role.name, role.description) for role in self.resolve_subagent_roles())
            if any(spec.tool.name == 'subagent' for spec in view) and self.resolve_subagent_roles is not None else ())
        return {'tool_execution': {'tool_names': [spec.tool.name for spec in view]}}

    def _inputs(self, state, runtime):
        specs = batch_tool_specs(self.registry, state, runtime.context)
        sid = runtime.context.scope.identity.session_id
        parts = self.resolve_prompt_parts(sid, runtime.context.scope.identity.agent_path) if self.resolve_prompt_parts is not None else []
        prompt = compose_model_prompt(self.system_prompt, parts, specs, (runtime.context.invocation.model_call.subagent_roles if runtime.context.invocation.model_call is not None else ()))
        tools = []
        for spec in specs:
            if isinstance(spec.input_schema, dict):
                contract = spec._checked_json_input()
                # Keep arbitrary JSON Schema/data intact. BaseTool conversion
                # rewrites refs; the public function-dict input passes through.
                tools.append({'type': 'function', 'function': {
                    'name': spec.tool.name, 'description': spec.tool.description,
                    'parameters': deepcopy(contract.schema)}})
            else:
                tools.append(spec.tool)
        return tools, prompt

    def _tool_request(self, request):
        specs = batch_tool_specs(self.registry, request.state, request.runtime.context)
        spec = next((spec for spec in specs if spec.tool.name == request.tool_call['name']), None)
        if spec is not None and isinstance(spec.input_schema, dict):
            spec._checked_json_input()
        return (request.override(tool=spec.tool), spec) if spec else None

    @staticmethod
    def _unavailable(request):
        return ToolMessage(content='Tool is not active for this model request. Choose an available tool.',
            name=request.tool_call['name'], tool_call_id=request.tool_call['id'], status='error',
            additional_kwargs={'naiyue_not_executed': 'tool_not_active'})


    async def awrap_tool_call(self, request, handler):
        selected = self._tool_request(request)
        if selected is not None:
            from naiyue_test_agent.mcp_client.presentation import complete_result
            from naiyue_test_agent.mcp_client.tools import MCPPermission
            selected_request, spec = selected
            if isinstance(spec.permissions, MCPPermission):
                prepared = next((call for call in request.state.get('tool_execution', {}).get('calls', [])
                                 if call['id'] == request.tool_call['id']), None)
                reason = spec.permissions.unavailable_reason()
                if prepared is not None and prepared.get('subject', {}).get('mcp') not in (None, spec.permissions.identity()):
                    reason = 'definition_changed'
                if reason:
                    async def unavailable(_):
                        return ToolMessage(content='MCP tool is disabled or its definition changed. '
                            'No tools/call was sent. Request a new call after reviewing the current tools.',
                            name=request.tool_call['name'], tool_call_id=request.tool_call['id'], status='error',
                            additional_kwargs={'naiyue_not_executed': reason})
                    return complete_result(await aobserve_tool_dispatch(request, handler, unavailable), spec.permissions)
            return complete_result(await handler(selected_request), spec.permissions)
        async def denied(_):
            return self._unavailable(request)
        return await aobserve_tool_dispatch(request, handler, denied)
