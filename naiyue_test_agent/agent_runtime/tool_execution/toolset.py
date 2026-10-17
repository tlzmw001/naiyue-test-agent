"""One request's original definitions, shared by prompt, budget and execution."""

from typing import NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import SystemMessage, ToolMessage

from .observation import aobserve_tool_dispatch


_VIEW_KEY = '_naiyue_tool_view'
_FIRST_KEY = '_naiyue_first_tool_view'


class ToolExecutionState(AgentState):
    tool_execution: NotRequired[dict]


def batch_tool_specs(registry, state, context):
    names = state.get('tool_execution', {}).get('tool_names')
    if names is None:
        raise RuntimeError('Current tool selection is unavailable')
    view = context.get(_VIEW_KEY)
    if view is not None and tuple(spec.tool.name for spec in view) == tuple(names):
        return view
    return registry.select(names)


def compose_tool_prompt(base, project, specs):
    prompt = base + (project or '')
    if not specs:
        return prompt
    summaries = [f'{spec.tool.name}: {spec.prompt_snippet or spec.tool.description}' for spec in specs]
    guidelines = dict.fromkeys(line for spec in specs for line in spec.prompt_guidelines if line)
    return prompt + '\n\nAvailable tools:\n' + '\n'.join(summaries) + (
        '\n\nTool guidelines:\n' + '\n'.join(guidelines) if guidelines else '')


class ToolSetMiddleware(AgentMiddleware):
    state_schema = ToolExecutionState

    def __init__(self, registry, resolve_active_tool_names, system_prompt, resolve_project_instructions=None):
        self.registry = registry
        self.resolve_active_tool_names = resolve_active_tool_names
        self.system_prompt = system_prompt
        self.resolve_project_instructions = resolve_project_instructions

    def prepare_tool_view(self, runtime):
        context = runtime.context
        names = self.resolve_active_tool_names(context['session_id'], context.get('agent_path', 'root'))
        view = self.registry.select(names)
        context[_VIEW_KEY] = view
        return {'tool_execution': {'tool_names': [spec.tool.name for spec in view]}}

    def before_agent(self, state, runtime):
        update = self.prepare_tool_view(runtime)
        runtime.context[_FIRST_KEY] = True
        return update

    async def abefore_agent(self, state, runtime):
        return self.before_agent(state, runtime)

    def before_model(self, state, runtime):
        if runtime.context.pop(_FIRST_KEY, False):
            return None
        return self.prepare_tool_view(runtime)

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)

    def model_inputs(self, state, runtime):
        specs = batch_tool_specs(self.registry, state, runtime.context)
        project = (self.resolve_project_instructions(runtime.context['session_id'])
                   if self.resolve_project_instructions is not None else None)
        return [spec.tool for spec in specs], compose_tool_prompt(self.system_prompt, project, specs)

    def _model_request(self, request):
        tools, prompt = self.model_inputs(request.state, request.runtime)
        return request.override(tools=tools, system_message=SystemMessage(content=prompt))


    async def awrap_model_call(self, request, handler):
        return await handler(self._model_request(request))

    def _tool_request(self, request):
        specs = batch_tool_specs(self.registry, request.state, request.runtime.context)
        spec = next((spec for spec in specs if spec.tool.name == request.tool_call['name']), None)
        return request.override(tool=spec.tool) if spec else None

    @staticmethod
    def _unavailable(request):
        return ToolMessage(content='Tool is not active for this model request. Choose an available tool.',
            name=request.tool_call['name'], tool_call_id=request.tool_call['id'], status='error',
            additional_kwargs={'naiyue_not_executed': 'tool_not_active'})


    async def awrap_tool_call(self, request, handler):
        selected = self._tool_request(request)
        if selected is not None:
            return await handler(selected)
        async def denied(_):
            return self._unavailable(request)
        return await aobserve_tool_dispatch(request, handler, denied)
