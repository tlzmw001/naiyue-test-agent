"""Model response gates, model observations, and shared event helpers."""

from .observation import observe, emit_event

import asyncio
import logging
import re
from collections.abc import Callable
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp
from .run_control import UserStop, native_async_model

from naiyue_test_agent.agent_runtime.events import (
    ModelCallStartedData, ModelCallFinishedData,
    RunEventPublisher, safe_error_type,
)
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
