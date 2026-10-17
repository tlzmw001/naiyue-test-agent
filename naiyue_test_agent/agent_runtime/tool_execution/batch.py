"""Run-scoped ordering for synchronous client-side tool-call batches."""

import logging
from threading import Condition

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from ..errors import ModelResponseRejected, ToolBatchSchedulingError
from naiyue_test_agent.agent_runtime.tool_execution.observation import observe_tool_dispatch, aobserve_tool_dispatch, observe_tool_progress
from .async_batch import AsyncBatchPlan
from .toolset import batch_tool_specs
from ..run_control import stop_tool_message
from naiyue_test_agent.tools.registry import ToolRegistry


logger = logging.getLogger(__name__)
_PLAN_KEY = '_naiyue_tool_batch_plan'


class _SequentialPlan:
    def __init__(self, call_ids: list[str], run_id: str | None):
        self.call_ids = tuple(call_ids)
        self.run_id = run_id
        self.position = 0
        self.failed = False
        self.condition = Condition()

    def execute(self, call_id: str, tool_name: str, handler):
        with self.condition:
            if call_id not in self.call_ids:
                raise ToolBatchSchedulingError('Tool call is outside the current batch.')
            if self.call_ids.index(call_id) < self.position:
                raise ToolBatchSchedulingError('Tool call already completed.')
            while not self.failed and self.call_ids[self.position] != call_id:
                self.condition.wait()
            if self.failed:
                logger.error('tool_batch_skipped', extra={'tool_batch_event': {
                    'event': 'tool_batch_skipped', 'tool_call_id': call_id,
                    'tool_name': tool_name, 'run_id': self.run_id,
                }})
                return ToolMessage(
                    content='Tool was not executed because an earlier call in this batch failed.',
                    tool_call_id=call_id, name=tool_name, status='error',
                )

        try:
            result = handler()
        except BaseException:
            with self.condition:
                self.failed = True
                self.condition.notify_all()
            raise
        with self.condition:
            self.position += 1
            self.condition.notify_all()
        return result


class ToolBatchMiddleware(AgentMiddleware):
    """Keep read-only batches parallel and serialize batches containing marked tools."""

    def __init__(self, tool_registry: ToolRegistry):
        self.registry = tool_registry

    def after_model(self, state, runtime):
        context = runtime.context
        context[_PLAN_KEY] = None
        modes = self._modes(state, context)
        registered = self._registered_pending(state, modes)
        if not any(modes[call['name']] == 'sequential' for call in registered):
            return None
        context[_PLAN_KEY] = _SequentialPlan(
            [call['id'] for call in registered], context.get('run_id'),
        )
        return None

    async def aafter_model(self, state, runtime):
        context = runtime.context
        modes = self._modes(state, context)
        registered = self._registered_pending(state, modes)
        context[_PLAN_KEY] = (AsyncBatchPlan(
            [call['id'] for call in registered],
            any(modes[call['name']] == 'sequential' for call in registered),
            context.get('run_id'), ToolBatchSchedulingError,
        ) if registered else None)

    def _modes(self, state, context):
        return {spec.tool.name: spec.execution_mode for spec in batch_tool_specs(self.registry, state, context)}

    def _registered_pending(self, state, modes):
        messages = state['messages']
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], AIMessage):
                last_ai = messages[index]
                completed = {
                    item.tool_call_id for item in messages[index + 1:]
                    if isinstance(item, ToolMessage)
                }
                break
        else:
            return []

        pending = [call for call in last_ai.tool_calls if call['id'] not in completed]
        ids = [call.get('id') for call in pending]
        if any(not isinstance(call_id, str) or not call_id for call_id in ids):
            raise ModelResponseRejected('missing_tool_call_id')
        if len(set(ids)) != len(ids):
            raise ModelResponseRejected('duplicate_tool_call_ids')

        return [call for call in pending if call['name'] in modes]

    def wrap_tool_call(self, request, handler):
        return observe_tool_dispatch(
            request, handler, lambda inner: self._dispatch_tool_call(request, inner),
        )

    async def awrap_tool_call(self, request, handler):
        return await aobserve_tool_dispatch(
            request, handler, lambda inner: self._adispatch_tool_call(request, inner),
        )

    async def _adispatch_tool_call(self, request, handler):
        context = request.runtime.context or {}
        plan = context.get(_PLAN_KEY)
        observe_tool_progress(context, 'report_dispatch', request, 'queued')
        async def guarded():
            control = context.get('_run_control')
            if control is not None and control.requested:
                return stop_tool_message(request.tool_call)
            observe_tool_progress(context, 'report_dispatch', request, 'preparing')
            return await handler(request)
        if request.tool_call['name'] not in self._modes(request.state, context):
            return await guarded()
        if not isinstance(plan, AsyncBatchPlan):
            raise ToolBatchSchedulingError('Tool batch plan is unavailable.')
        return await plan.execute(request.tool_call['id'], request.tool_call['name'],
            guarded, request.runtime.config.get('max_concurrency', 4))

    def _dispatch_tool_call(self, request, handler):
        context = request.runtime.context or {}
        plan = context.get(_PLAN_KEY)
        observe_tool_progress(context, 'report_dispatch', request, 'queued')

        def guarded():
            control = context.get('_run_control')
            if control is not None and control.requested:
                return stop_tool_message(request.tool_call)
            observe_tool_progress(context, 'report_dispatch', request, 'preparing')
            return handler(request)

        if plan is None:
            modes = self._modes(request.state, context or {})
            pending = self._registered_pending(request.state, modes)
            if (pending
                    and any(call['id'] == request.tool_call['id']
                            for call in pending)
                    and any(modes.get(call['name']) == 'sequential' for call in pending)):
                logger.error('tool_batch_plan_missing', extra={'tool_batch_event': {
                    'event': 'tool_batch_plan_missing',
                    'tool_call_id': request.tool_call['id'],
                    'tool_name': request.tool_call['name'],
                    'run_id': context.get('run_id') if context else None,
                }})
                raise ToolBatchSchedulingError('Tool batch plan is unavailable.')
            return guarded()
        if request.tool_call['name'] not in self._modes(request.state, context or {}):
            return guarded()
        return plan.execute(
            request.tool_call['id'], request.tool_call['name'],
            guarded,
        )
