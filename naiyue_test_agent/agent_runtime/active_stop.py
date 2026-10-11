"""Stop model waits through middleware, then finish the existing compiled graph."""

from .observation import observe, emit_event

from langchain.agents.middleware import AgentMiddleware, ExtendedModelResponse, ModelResponse, hook_config
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from .events import RunFailureData, ToolCallStartedData, ToolCallFinishedData
from .run_control import UserStop, stop_tool_message
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected


class StopModelMiddleware(AgentMiddleware):
    def wrap_model_call(self, request, handler):
        return handler(request)

    async def awrap_model_call(self, request, handler):
        control = request.runtime.context.get('_run_control')
        if control is None:
            return await handler(request)
        try:
            return await handler(request)
        except UserStop:
            turns = request.runtime.context.get('_turn_observer')
            if turns is not None:
                observe(turns, turns.fail, RunFailureData('execute', 'cancelled', 'user_stop', 'CancelledError'))
            return ExtendedModelResponse(ModelResponse(result=[]),
                                         Command(update={'run_stopped': True}))


class StopBoundaryMiddleware(AgentMiddleware):
    @hook_config(can_jump_to=['end'])
    def after_model(self, state, runtime):
        control = runtime.context.get('_run_control')
        if control is None or not control.requested:
            return None
        results = []
        messages = state['messages']
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], AIMessage):
                completed = {m.tool_call_id for m in messages[index + 1:] if isinstance(m, ToolMessage)}
                pending = [c for c in messages[index].tool_calls if c['id'] not in completed]
                ids = [c.get('id') for c in pending]
                if any(not isinstance(identity, str) or not identity for identity in ids):
                    raise ModelResponseRejected('missing_tool_call_id')
                if len(set(ids)) != len(ids):
                    raise ModelResponseRejected('duplicate_tool_call_ids')
                results = [stop_tool_message(c) for c in pending]
                break
        publisher = runtime.context.get('_run_event_publisher')
        if publisher is not None:
            from .call_observation import event_label
            for message in results:
                name = event_label(message.name)
                emit_event(publisher, 'tool_call_started', lambda: ToolCallStartedData(message.tool_call_id, name))
                emit_event(publisher, 'tool_call_finished', lambda: ToolCallFinishedData(
                    message.tool_call_id, name, 'dispatch', 'not_executed', 'user_stop'))
        turns = runtime.context.get('_turn_observer')
        if turns is not None:
            observe(turns, turns.mark_ready)
        return {'messages': results, 'run_stopped': True, 'jump_to': 'end'}
