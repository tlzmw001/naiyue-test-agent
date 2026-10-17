"""Tool execution guards, legacy logs, and dispatch event observations."""

import asyncio
import logging
from threading import RLock

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.tools.processes import ProcessCleanupError
from naiyue_test_agent.model_access.stop_reason import MODEL_STOP_REASON_KEY
from ..call_observation import event_label, _emit
from ..errors import describe_failure_in_context, run_failure_data
from ..events import ToolCallStartedData, ToolCallFinishedData
from ..stream_events import ToolProgressData
from naiyue_test_agent.tools.settings import ToolProgressSettings


logger = logging.getLogger('naiyue_test_agent.agent_runtime')


class ToolProgressObserver:
    """Run-local call identity and termination, without presentation snapshots."""

    def __init__(self, publisher, settings=None):
        self.publisher = publisher
        self.settings = settings if settings is not None else ToolProgressSettings()
        self._batch = None
        self._calls = {}
        self._lock = RLock()
        self._diagnosed = False

    @staticmethod
    def identity(request):
        return (request.state.get('tool_execution', {}).get('batch_id'),
                request.tool_call['id'], request.tool_call['name'])

    def begin_dispatch(self, request):
        batch, call, name = self.identity(request)
        if not self.settings.enabled or not isinstance(batch, str) or not batch:
            return
        with self._lock:
            if batch != self._batch:
                self._batch, self._calls = batch, {}
            self._calls[call] = (name, False)

    def report_dispatch(self, request, activity):
        batch, call, name = self.identity(request)
        self.accept_custom({'kind': 'naiyue.tool_progress', 'version': 1,
            'batch_id': batch, 'tool_call_id': call, 'tool_name': name, 'activity': activity,
            'phase': 'batch_wait' if activity == 'queued' else 'authorize'})

    def _drop(self, reason):
        if not self._diagnosed:
            self._diagnosed = True
            logger.warning('tool_progress_dropped reason=%s', reason)

    def accept_custom(self, payload):
        if not self.settings.enabled or not isinstance(payload, dict) or payload.get('kind') != 'naiyue.tool_progress':
            return
        with self._lock:
            try:
                if type(payload.get('version')) is not int or payload['version'] != 1:
                    raise ValueError('Invalid progress version')
                data = ToolProgressData(**{k: v for k, v in payload.items() if k not in ('kind', 'version')})
                if len(data.text.encode('utf-8')) > self.settings.max_text_bytes:
                    raise ValueError('Progress text budget exceeded')
            except (TypeError, ValueError, UnicodeError):
                self._drop('invalid_payload')
                return
            if data.batch_id != self._batch or self._calls.get(data.tool_call_id) != (data.tool_name, False):
                self._drop('unregistered_or_closed')
                return
            try:
                self.publisher.publish('tool_progress', data)
            except GraphBubbleUp:
                raise
            except Exception as error:
                self._drop(type(error).__name__)

    def finish(self, batch_id, call_id):
        with self._lock:
            if batch_id == self._batch and call_id in self._calls:
                name, _ = self._calls[call_id]
                self._calls[call_id] = (name, True)


def observe_tool_progress(context, operation, *args):
    """Optional observation must preserve graph controls and the primary work."""
    observer = (context or {}).get('_tool_progress_observer')
    if observer is None:
        return
    try:
        getattr(observer, operation)(*args)
    except GraphBubbleUp:
        raise
    except Exception as error:
        if not observer._diagnosed:
            observer._diagnosed = True
            logger.warning('tool_progress_unavailable error_type=%s', type(error).__name__)


def _prepare_tool_call(request):
    context = request.runtime.context
    run_id = context.get('run_id') if context else None
    correlation = {'run_id': run_id} if run_id else {}
    tool_fields = {
        'tool_name': request.tool_call['name'],
        'tool_call_id': request.tool_call['id'],
        **correlation,
    }

    def record(event: str) -> None:
        logger.log(
            logging.ERROR if event == 'tool_failed' else logging.INFO,
            event,
            extra={'tool_event': {'event': event, **tool_fields}},
        )

    record('tool_started')
    guard_reason = tool_guard_reason(request)
    failure_message = None
    if guard_reason == 'tool_call_mismatch':
        failure_message = ('Tool call was not executed because it did not match the '
                           'latest model response. Reissue the complete tool call.')
    elif guard_reason == 'model_response_truncated':
        failure_message = ('Tool call was not executed because the model response was '
                           'truncated. Reissue the complete tool call.')
    if failure_message is not None:
        record('tool_failed')
        return record, ToolMessage(
            content=failure_message,
            tool_call_id=request.tool_call['id'],
            name=request.tool_call['name'],
            status='error',
        )
    return record, None


class _ToolCallObservationMiddleware(AgentMiddleware):
    @property
    def name(self):
        return '_record_tool_call'


    async def awrap_tool_call(self, request, handler):
        from ..async_utils import await_settled
        record, denied = _prepare_tool_call(request)
        if denied is not None:
            return denied
        try:
            result = await await_settled(handler(request))
        except BaseException:
            record('tool_failed')
            raise
        record('tool_failed' if isinstance(result, ToolMessage) and result.status == 'error'
               else 'tool_completed')
        return result


_record_tool_call = _ToolCallObservationMiddleware()

def tool_guard_reason(request):
    messages = request.state.get('messages', []) if isinstance(request.state, dict) else []
    last_ai = next((item for item in reversed(messages) if isinstance(item, AIMessage)), None)
    if last_ai is None or not any(call['id'] == request.tool_call['id'] for call in last_ai.tool_calls):
        return 'tool_call_mismatch'
    if last_ai.response_metadata.get(MODEL_STOP_REASON_KEY) == 'length':
        return 'model_response_truncated'
    return None


async def aobserve_tool_dispatch(request, handler, dispatch):
    context = request.runtime.context
    call_id, name = request.tool_call['id'], event_label(request.tool_call['name'])
    _emit(context, 'tool_call_started', lambda: ToolCallStartedData(call_id, name))
    observe_tool_progress(context, 'begin_dispatch', request)
    entered = False

    async def enter_chain(inner_request):
        nonlocal entered
        entered = True
        return await handler(inner_request)

    try:
        try:
            result = await dispatch(enter_chain)
        finally:
            batch = request.state.get('tool_execution', {}).get('batch_id')
            observe_tool_progress(context, 'finish', batch, call_id)
    except GraphBubbleUp:
        raise
    except (Exception, asyncio.CancelledError) as error:
        outcome = 'outcome_unknown' if isinstance(error, ProcessCleanupError) else 'exception'
        description = describe_failure_in_context(error, context, source='tool')
        _emit(context, 'tool_call_finished', lambda: ToolCallFinishedData(
            call_id, name, 'dispatch', outcome, failure=run_failure_data(description),
        ), primary_error=error)
        raise
    reason = (result.additional_kwargs.get('naiyue_not_executed') if isinstance(result, ToolMessage) else None)
    reason = reason or (tool_guard_reason(request) if entered else 'prior_batch_failed')
    outcome = ('not_executed' if reason is not None else 'error_result'
               if isinstance(result, ToolMessage) and result.status == 'error' else 'success')
    _emit(context, 'tool_call_finished', lambda: ToolCallFinishedData(call_id, name, 'dispatch', outcome, reason))
    return result
