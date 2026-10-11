"""Model response gates, legacy logs, and model/tool call event observations."""

from .observation import observe, emit_event

import asyncio
import logging
import re
from collections.abc import Callable
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware, ModelRequest, wrap_tool_call, hook_config
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp, GraphInterrupt
from .run_control import UserStop, native_async_model

from naiyue_test_agent.agent_runtime.events import (
    ModelCallStartedData, ModelCallFinishedData, ToolCallStartedData, ToolCallFinishedData,
    RunEventPublisher, RunFailureData, safe_error_type,
)
from naiyue_test_agent.agent_runtime.tools.write import WriteOutcomeUnknown
from naiyue_test_agent.agent_runtime.tools.bash import BashOutcomeUnknown
from naiyue_test_agent.model_access.errors import (
    unknown_failure_diagnostics,
)
from .errors import (
    ModelResponseRejected,
    describe_failure_in_context, run_failure_data,
)
from naiyue_test_agent.model_access.stop_reason import (
    MODEL_STOP_REASON_KEY, normalize_stop_reason, unknown_stop_reason_diagnostics,
)


logger = logging.getLogger('naiyue_test_agent.agent_runtime')


def make_model_selection_middleware(
    resolve_model: Callable[[str], tuple[str, BaseChatModel]],
    record_selection: Callable[[str, str], None],
):
    """Choose the session model with the same gates in sync and async calls."""
    return _ModelSelectionMiddleware(resolve_model, record_selection)


class _ModelSelectionMiddleware(AgentMiddleware):
    def __init__(self, resolve_model, record_selection):
        self.resolve_model = resolve_model
        self.record_selection = record_selection

    @property
    def name(self):
        return 'select_model'

    def _prepare(self, request):
        context = request.runtime.context
        session_id = context.get('session_id') if context else None
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('A non-empty session_id is required')
        run_id = context.get('run_id') if context else None
        correlation = {'run_id': run_id} if run_id else {}

        model_id, model = self.resolve_model(session_id)
        if not model_id:
            raise ValueError('Resolved model_id must not be empty')
        self.record_selection(session_id, model_id)
        if run_id:
            logger.info(
                'model_selected',
                extra={'model_selection': {
                    'event': 'model_selected', 'model_id': model_id, **correlation,
                }},
            )
        call_id = uuid4().hex
        context_operations = context.get('_context_observer')
        if context_operations is not None:
            observe(context_operations, context_operations.model_call_started, call_id)
        turns = context.get('_turn_observer')
        if turns is not None:
            observe(turns, turns.begin, call_id)
        _emit(context, 'model_call_started', lambda: ModelCallStartedData(call_id, event_label(model_id)))
        return model_id, call_id, model

    def _accept(self, request, response, model_id, call_id):
        context = request.runtime.context
        run_id = context.get('run_id')
        correlation = {'run_id': run_id} if run_id else {}
        context_operations = context.get('_context_observer')
        turns = context.get('_turn_observer')
        message = next((item for item in response.result if isinstance(item, AIMessage)), None)
        if message is not None:
            message.response_metadata[MODEL_STOP_REASON_KEY] = normalize_stop_reason(message)
            if message.response_metadata[MODEL_STOP_REASON_KEY] == 'unknown':
                logger.warning(
                    'model_stop_reason_unknown',
                    extra={'model_response': {
                        'event': 'model_stop_reason_unknown', 'model_id': model_id,
                        'normalized_reason': 'unknown',
                        **unknown_stop_reason_diagnostics(message),
                        'valid_tool_call_count': len(message.tool_calls),
                        'invalid_tool_call_count': len(message.invalid_tool_calls),
                        **correlation,
                    }},
                )
        usage = message.usage_metadata if message is not None else None
        details = (usage.get('input_token_details') or {}) if usage else {}
        input_tokens = usage.get('input_tokens') if usage else None
        cache_read_tokens = details.get('cache_read')
        logger.info(
            'model_call_usage input_tokens=%s cache_read_tokens=%s',
            input_tokens, cache_read_tokens,
            extra={'model_usage': {
                'event': 'model_call_usage', 'model_id': model_id,
                'input_tokens': input_tokens, 'cache_read_tokens': cache_read_tokens,
                **correlation,
            }},
        )

        if message is None:
            raise ModelResponseRejected('missing_ai_message')
        if message.response_metadata[MODEL_STOP_REASON_KEY] == 'error':
            raise ModelResponseRejected('stop_reason_error')
        if message.invalid_tool_calls:
            raise ModelResponseRejected('invalid_tool_calls')
        if (message.response_metadata[MODEL_STOP_REASON_KEY] == 'tool_use'
                and not message.tool_calls):
            raise ModelResponseRejected('missing_tool_calls')
        if turns is not None:
            observe(turns, turns.accept, message)
        if context_operations is not None:
            observe(context_operations, context_operations.model_call_accepted, call_id)
        _emit(context, 'model_call_finished', lambda: ModelCallFinishedData(
            call_id, event_label(model_id), 'accepted',
            message.response_metadata[MODEL_STOP_REASON_KEY],
            tuple(call['id'] for call in message.tool_calls if isinstance(call.get('id'), str)),
            token_count(input_tokens), token_count(usage.get('output_tokens') if usage else None),
            token_count(cache_read_tokens),
        ))
        return response

    def _failed(self, request, error, model_id, call_id):
        context = request.runtime.context
        run_id = context.get('run_id')
        correlation = {'run_id': run_id} if run_id else {}
        context_operations = context.get('_context_observer')
        if context_operations is not None:
            observe(context_operations, context_operations.model_call_failed, call_id, error)
        description = describe_failure_in_context(error, context, source='model', model_id=model_id)
        category, reason = description.category, description.reason
        details = {}
        if category == 'unknown':
            try:
                details = unknown_failure_diagnostics(error)
            except Exception as fault:
                logger.error('call_failure_diagnostics_unavailable error_type=%s', safe_error_type(fault))
            details['error_type'] = description.error_type
        logger.error(
            'model_call_failed category=%s reason=%s', category, reason,
            extra={'model_failure': {
                'event': 'model_call_failed', 'model_id': model_id,
                'category': category, 'reason': reason, **details, **correlation,
            }},
        )
        if not isinstance(error, GraphBubbleUp):
            failure = run_failure_data(description)
            _emit(context, 'model_call_finished', lambda: ModelCallFinishedData(
                call_id, event_label(model_id), 'failed', failure=failure,
            ), primary_error=error)

    def wrap_model_call(self, request, handler):
        model_id, call_id, model = self._prepare(request)
        try:
            response = handler(request.override(model=model))
            return self._accept(request, response, model_id, call_id)
        except Exception as error:
            self._failed(request, error, model_id, call_id)
            raise

    async def awrap_model_call(self, request, handler):
        model_id, call_id, model = self._prepare(request)
        try:
            control = request.runtime.context.get('_run_control')
            if control is not None and native_async_model(model):
                response = await control.await_model(lambda: handler(request.override(model=model)))
            else:
                response = await handler(request.override(model=model))
            return self._accept(request, response, model_id, call_id)
        except (Exception, asyncio.CancelledError, UserStop) as error:
            self._failed(request, error, model_id, call_id)
            raise


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

    def wrap_tool_call(self, request, handler):
        record, denied = _prepare_tool_call(request)
        if denied is not None:
            return denied
        try:
            result = handler(request)
        except Exception:
            record('tool_failed')
            raise
        record('tool_failed' if isinstance(result, ToolMessage) and result.status == 'error'
               else 'tool_completed')
        return result

    async def awrap_tool_call(self, request, handler):
        from .async_utils import await_settled
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

def event_label(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', value) else None


def token_count(value):
    return value if type(value) is int and value >= 0 else None


def _emit(context, event_type, data, *, primary_error=None):
    publisher = context.get('_run_event_publisher') if isinstance(context, dict) else None
    if (not isinstance(publisher, RunEventPublisher)
            or not publisher.matches(context.get('session_id'), context.get('run_id'))):
        return
    emit_event(publisher, event_type, data)


def tool_guard_reason(request):
    messages = request.state.get('messages', []) if isinstance(request.state, dict) else []
    last_ai = next((item for item in reversed(messages) if isinstance(item, AIMessage)), None)
    if last_ai is None or not any(call['id'] == request.tool_call['id'] for call in last_ai.tool_calls):
        return 'tool_call_mismatch'
    if last_ai.response_metadata.get(MODEL_STOP_REASON_KEY) == 'length':
        return 'model_response_truncated'
    return None


def observe_tool_dispatch(request, handler, dispatch):
    context = request.runtime.context
    call_id = request.tool_call['id']
    name = event_label(request.tool_call['name'])
    _emit(context, 'tool_call_started', lambda: ToolCallStartedData(call_id, name))
    entered = False

    def enter_chain(inner_request):
        nonlocal entered
        entered = True
        return handler(inner_request)

    try:
        result = dispatch(enter_chain)
    except GraphBubbleUp:
        raise
    except Exception as error:
        outcome = 'outcome_unknown' if isinstance(error, (WriteOutcomeUnknown, BashOutcomeUnknown)) else 'exception'
        description = describe_failure_in_context(error, context, source='tool')
        _emit(context, 'tool_call_finished', lambda: ToolCallFinishedData(
            call_id, name, 'dispatch', outcome, failure=run_failure_data(description),
        ), primary_error=error)
        raise
    guard_reason = (result.additional_kwargs.get('naiyue_not_executed') if isinstance(result, ToolMessage) else None)
    guard_reason = guard_reason or (tool_guard_reason(request) if entered else 'prior_batch_failed')
    if guard_reason is not None:
        outcome, reason = 'not_executed', guard_reason
    else:
        outcome = 'error_result' if isinstance(result, ToolMessage) and result.status == 'error' else 'success'
        reason = None
    _emit(context, 'tool_call_finished', lambda: ToolCallFinishedData(call_id, name, 'dispatch', outcome, reason))
    return result


async def aobserve_tool_dispatch(request, handler, dispatch):
    context = request.runtime.context
    call_id, name = request.tool_call['id'], event_label(request.tool_call['name'])
    _emit(context, 'tool_call_started', lambda: ToolCallStartedData(call_id, name))
    entered = False

    async def enter_chain(inner_request):
        nonlocal entered
        entered = True
        return await handler(inner_request)

    try:
        result = await dispatch(enter_chain)
    except GraphBubbleUp:
        raise
    except (Exception, asyncio.CancelledError) as error:
        outcome = 'outcome_unknown' if isinstance(error, (WriteOutcomeUnknown, BashOutcomeUnknown)) else 'exception'
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


class ObservedHumanInTheLoopMiddleware(HumanInTheLoopMiddleware):
    @staticmethod
    def _stop_update(state, runtime):
        from .active_stop import StopBoundaryMiddleware
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
        from .async_utils import run_blocking
        return await run_blocking(self.after_model, state, runtime)
