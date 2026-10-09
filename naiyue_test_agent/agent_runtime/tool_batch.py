"""Run-scoped ordering for synchronous client-side tool-call batches."""

import logging
import math
import os
from threading import Condition
from time import monotonic

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.model_access.errors import ModelResponseRejected
from naiyue_test_agent.agent_runtime.call_observation import observe_tool_dispatch, aobserve_tool_dispatch
from .async_tool_batch import AsyncBatchPlan
from .run_control import stop_tool_message


logger = logging.getLogger(__name__)
_MODE_KEY = 'naiyue_execution_mode'
_PLAN_KEY = '_naiyue_tool_batch_plan'
_WAIT_ENV = 'NAIYUE_TOOL_BATCH_WAIT_SECONDS'


class ToolBatchSchedulingError(RuntimeError):
    """The current tool batch cannot make safe scheduling progress."""


class _SequentialPlan:
    def __init__(self, call_ids: list[str], wait_seconds: float, run_id: str | None):
        self.call_ids = tuple(call_ids)
        self.wait_seconds = wait_seconds
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
            deadline = monotonic() + self.wait_seconds
            while not self.failed and self.call_ids[self.position] != call_id:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self.failed = True
                    self.condition.notify_all()
                    logger.error('tool_batch_wait_failed', extra={'tool_batch_event': {
                        'event': 'tool_batch_wait_failed', 'tool_call_id': call_id,
                        'tool_name': tool_name, 'run_id': self.run_id,
                    }})
                    raise ToolBatchSchedulingError('Tool batch ordering timed out.')
                self.condition.wait(remaining)
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

    def __init__(self, tools: list):
        self.modes = {}
        for item in tools:
            name = getattr(item, 'name', None)
            metadata = getattr(item, 'metadata', None) or {}
            mode = metadata.get(_MODE_KEY, 'parallel')
            if mode not in ('parallel', 'sequential'):
                raise ValueError('Invalid tool execution mode')
            if isinstance(name, str):
                self.modes[name] = mode

        raw_wait = os.environ.get(_WAIT_ENV, '60')
        try:
            self.wait_seconds = float(raw_wait)
        except ValueError as error:
            raise ValueError('Invalid tool batch wait duration') from error
        if not math.isfinite(self.wait_seconds) or self.wait_seconds <= 0:
            raise ValueError('Invalid tool batch wait duration')

    def after_model(self, state, runtime):
        context = runtime.context
        context[_PLAN_KEY] = None
        registered = self._registered_pending(state)
        if not any(self.modes[call['name']] == 'sequential' for call in registered):
            return None
        context[_PLAN_KEY] = _SequentialPlan(
            [call['id'] for call in registered], self.wait_seconds, context.get('run_id'),
        )
        return None

    async def aafter_model(self, state, runtime):
        context = runtime.context
        registered = self._registered_pending(state)
        context[_PLAN_KEY] = (AsyncBatchPlan(
            [call['id'] for call in registered],
            any(self.modes[call['name']] == 'sequential' for call in registered),
            self.wait_seconds, context.get('run_id'), ToolBatchSchedulingError,
        ) if registered else None)

    def _registered_pending(self, state):
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

        return [call for call in pending if call['name'] in self.modes]

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
        async def guarded():
            control = context.get('_run_control')
            if control is not None and control.requested:
                return stop_tool_message(request.tool_call)
            return await handler(request)
        if request.tool_call['name'] not in self.modes:
            return await guarded()
        if not isinstance(plan, AsyncBatchPlan):
            raise ToolBatchSchedulingError('Tool batch plan is unavailable.')
        return await plan.execute(request.tool_call['id'], request.tool_call['name'],
            guarded, request.runtime.config.get('max_concurrency', 4))

    def _dispatch_tool_call(self, request, handler):
        context = request.runtime.context
        plan = context.get(_PLAN_KEY) if context else None
        if plan is None:
            messages = request.state.get('messages', [])
            last_ai = next((item for item in reversed(messages)
                            if isinstance(item, AIMessage)), None)
            if (last_ai is not None
                    and any(call['id'] == request.tool_call['id']
                            for call in last_ai.tool_calls)
                    and any(self.modes.get(call['name']) == 'sequential'
                            for call in last_ai.tool_calls)):
                logger.error('tool_batch_plan_missing', extra={'tool_batch_event': {
                    'event': 'tool_batch_plan_missing',
                    'tool_call_id': request.tool_call['id'],
                    'tool_name': request.tool_call['name'],
                    'run_id': context.get('run_id') if context else None,
                }})
                raise ToolBatchSchedulingError('Tool batch plan is unavailable.')
            return handler(request)
        if request.tool_call['name'] not in self.modes:
            return handler(request)
        return plan.execute(
            request.tool_call['id'], request.tool_call['name'],
            lambda: handler(request),
        )
