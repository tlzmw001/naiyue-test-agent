"""Run feedback assertions through the actual runtime-owned conversion boundary."""

from naiyue_test_agent.application import prepare_tool_registry

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from naiyue_test_agent.agent_runtime import build_runtime_agent
from test_agent_runtime import ToolCapableFake


EXPECTED_NOASK = 'Tool was not executed: access denied by the permission policy (NoAsk).'


async def invoke_tool_feedback(spec, args, *, call_id, context=None, permission_mode='NoAsk'):
    tool = spec.tool
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{'name': tool.name, 'args': args, 'id': call_id}]),
        AIMessage(content='handled'),
    ])
    _runtime_registry_1 = prepare_tool_registry([spec])
    _runtime_names_1 = _runtime_registry_1.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
        resolve_model=lambda _: ('fake', model), record_selection=lambda *_: None,
        checkpointer=InMemorySaver(),
        resolve_permission_context=lambda _sid, agent_path='root': {'mode': permission_mode, 'session_allow': []},
    )
    config = {'configurable': {'thread_id': 'feedback'}}
    context = {'session_id': 'feedback', **(context or {})}
    result = (await agent.ainvoke({'messages': [('user', 'run tool')]},
                          context=context, config=config))
    feedback = next(m for m in result['messages']
                    if isinstance(m, ToolMessage) and m.tool_call_id == call_id)
    assert feedback.name == tool.name
    assert result['messages'][-1].content == 'handled'
    return feedback


def fail_tool_dispatch(monkeypatch, tool_name, failure):
    """Inject a runtime dispatch fault outside the ordinary execution-error adapter."""
    from naiyue_test_agent.agent_runtime.tool_execution.observation import _record_tool_call

    async_original = _record_tool_call.awrap_tool_call

    async def async_dispatch(request, handler):
        if request.tool_call['name'] != tool_name:
            return await async_original(request, handler)
        async def fail(_request):
            raise failure
        return await async_original(request, fail)

    monkeypatch.setattr(_record_tool_call, 'awrap_tool_call', async_dispatch)
