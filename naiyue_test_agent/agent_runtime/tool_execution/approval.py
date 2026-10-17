"""Observed official batch approval with the existing stop boundary."""

from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware, hook_config
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt

from ..call_observation import event_label, _emit
from ..events import ToolCallStartedData, ToolCallFinishedData
from ..observation import observe


class BatchApprovalMiddleware(AgentMiddleware):
    """Keep the graph node fixed while each batch gets its own official mapping."""

    def __init__(self, *, has_checkpointer):
        self.has_checkpointer = has_checkpointer

    def _local(self, state, runtime):
        calls = state.get('tool_execution', {}).get('calls', [])
        asks = {call['id']: call for call in calls if call.get('decision') == 'ask'}
        if asks and not self.has_checkpointer:
            raise ValueError('Session tool approval requires a checkpointer')
        def needs_review(request):
            return request.tool_call['id'] in asks
        return ObservedHumanInTheLoopMiddleware(interrupt_on={
            call['name']: {'allowed_decisions': ['approve', 'reject'],
                          'when': needs_review, 'description': 'Review access to the prepared tool target'}
            for call in asks.values()})


    @hook_config(can_jump_to=['end'])
    async def aafter_model(self, state, runtime):
        return await self._local(state, runtime).aafter_model(state, runtime)


class ObservedHumanInTheLoopMiddleware(HumanInTheLoopMiddleware):
    @staticmethod
    def _stop_update(state, runtime):
        from ..active_stop import StopBoundaryMiddleware
        control = (runtime.context or {}).get('_run_control')
        if control is not None and control.requested:
            return StopBoundaryMiddleware().after_model(state, runtime)
        return None

    def _create_action_and_config(self, tool_call, config, state, runtime):
        result = super()._create_action_and_config(tool_call, config, state, runtime)
        controls = runtime.context.get('_control_observer') if runtime.context else None
        if controls is not None:
            observe(controls, controls.note_approval_candidate, tool_call['id'])
        return result

    @hook_config(can_jump_to=['end'])
    def after_model(self, state, runtime):
        stopped = self._stop_update(state, runtime)
        if stopped is not None:
            return stopped
        controls = runtime.context.get('_control_observer') if runtime.context else None
        if controls is not None:
            observe(controls, controls.begin_approval_hook)
        try:
            update = super().after_model(state, runtime)
        except GraphInterrupt:
            stopped = self._stop_update(state, runtime)
            if stopped is not None:
                return stopped
            raise
        stopped = self._stop_update(state, runtime)
        if stopped is not None:
            return stopped
        if controls is not None:
            observe(controls, controls.approval_applied)
        for message in (update or {}).get('messages', []):
            if isinstance(message, ToolMessage) and message.status == 'error':
                call_id, name = message.tool_call_id, event_label(message.name)
                _emit(runtime.context, 'tool_call_started', lambda: ToolCallStartedData(call_id, name, 'approval_resolution'))
                _emit(runtime.context, 'tool_call_finished', lambda: ToolCallFinishedData(
                    call_id, name, 'approval_resolution', 'not_executed', 'approval_rejected',
                ))
        return update

    @hook_config(can_jump_to=['end'])
    async def aafter_model(self, state, runtime):
        from ..async_utils import run_blocking
        return await run_blocking(self.after_model, state, runtime)
