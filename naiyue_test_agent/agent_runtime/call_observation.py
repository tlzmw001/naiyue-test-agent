"""Model response gates, model observations, and shared event helpers."""

from naiyue_test_agent.contracts.failures import safe_error_type

from .observation import observe, emit_event

import asyncio
import logging
from naiyue_test_agent.contracts.run_context import ModelCallState, PreparedRequest, RuntimeContext
import re
from collections.abc import Callable
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from .run_control import UserStop, native_async_model
from .request_preparation import prepare_request

from naiyue_test_agent.agent_runtime.events import (
    RunEventPublisher, AgentEventPublisher,
)
from naiyue_test_agent.model_access.errors import (
    unknown_failure_diagnostics,
)
from naiyue_test_agent.model_access.usage import normalize_model_usage
from .errors import (
    ModelResponseRejected,
    describe_failure_in_context, run_failure_data,
)
from naiyue_test_agent.model_access.stop_reason import (
    MODEL_STOP_REASON_KEY, normalize_stop_reason, unknown_stop_reason_diagnostics,
)


logger = logging.getLogger('naiyue_test_agent.agent_runtime')


def make_model_selection_middleware(
    resolve_model: Callable[[str, str], tuple[str, BaseChatModel]],
    *, toolset=None, resolve_request_configuration=None, compact=False,
):
    """Prepare one model configuration and retain the existing response gates."""
    return _ModelSelectionMiddleware(resolve_model, toolset=toolset,
        resolve_request_configuration=resolve_request_configuration, compact=compact)


class _ModelSelectionMiddleware(AgentMiddleware):
    def __init__(self, resolve_model, *, toolset=None, resolve_request_configuration=None, compact=False):
        self.resolve_model = resolve_model
        self.toolset = toolset
        self.resolve_request_configuration = resolve_request_configuration
        self.compact = compact

    @property
    def name(self):
        return 'select_model'

    def _prepare(self, runtime):
        context = runtime.context
        session_id = context.scope.identity.session_id if context else None
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('A non-empty session_id is required')
        run_id = context.scope.identity.run_id if context else None
        correlation = {'run_id': run_id} if run_id else {}

        call_id = uuid4().hex
        context.invocation.model_call = ModelCallState(call_id)
        context.invocation.tool_batch = None
        turns = context.scope.observation.turns
        if turns is not None:
            observe(turns, turns.begin, call_id)
        context_operations = context.scope.observation.context_operations
        if context_operations is not None:
            observe(context_operations, context_operations.model_call_started, call_id)
        path = context.scope.identity.agent_path
        if self.resolve_request_configuration is not None:
            model_id, model, names = self.resolve_request_configuration(session_id, path)
        else:
            model_id, model = self.resolve_model(session_id, path)
            names = None
        if not model_id:
            raise ValueError('Resolved model_id must not be empty')
        if run_id:
            logger.info(
                'model_selected',
                extra={'model_selection': {
                    'event': 'model_selected', 'model_id': model_id, **correlation,
                }},
            )
        context.invocation.model_call.model_id = event_label(model_id)
        return PreparedRequest(model_id, call_id, model, active_tool_names=names)

    @hook_config(can_jump_to=['end'])
    async def abefore_model(self, state, runtime):
        context = runtime.context
        if not isinstance(context, RuntimeContext):
            raise ValueError('A non-empty session_id is required')
        control = context.scope.control
        if control is not None and control.requested:
            return {'run_stopped': True, 'jump_to': 'end'}
        try:
            prepared = self._prepare(runtime)
            return await prepare_request(state, runtime, prepared, toolset=self.toolset, compact=self.compact)
        except (Exception, asyncio.CancelledError, UserStop) as error:
            operations = context.scope.observation.context_operations
            if operations is not None:
                observe(operations, operations.model_call_failed, (context.invocation.model_call.call_id if context.invocation.model_call is not None else None), error)
            description = describe_failure_in_context(error, context, source='runtime')
            logger.error('model_request_preparation_failed error_type=%s', safe_error_type(error))
            turns = context.scope.observation.turns
            if turns is not None:
                observe(turns, turns.fail, run_failure_data(description))
            if isinstance(error, UserStop):
                return {'run_stopped': True, 'jump_to': 'end'}
            raise

    def _accept(self, request, response, model_id, call_id):
        context = request.runtime.context
        run_id = context.scope.identity.run_id
        correlation = {'run_id': run_id} if run_id else {}
        context_operations = context.scope.observation.context_operations
        turns = context.scope.observation.turns
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
        usage = normalize_model_usage(message.usage_metadata if message is not None else None)
        logger.info(
            'model_call_usage input_tokens=%s cache_read_tokens=%s cache_write_tokens=%s',
            usage['input_tokens'], usage['cache_read_tokens'], usage['cache_write_tokens'],
            extra={'model_usage': {
                'event': 'model_call_usage', 'model_id': model_id,
                **usage, **correlation,
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
        prepared = context.invocation.model_call.prepared if context.invocation.model_call else None
        notices = prepared.background_notices if prepared is not None else None
        tasks = context.scope.capabilities.process_tasks
        control = context.scope.control
        if notices is not None and tasks is not None and not (control is not None and control.requested):
            try:
                tasks.acknowledge_notices(notices, session_id=context.scope.identity.session_id,
                    workspace_root=context.scope.identity.execution_path, run_id=context.scope.identity.run_id)
            except Exception as error:
                logger.warning('background_notice_ack_failed error_type=%s', safe_error_type(error))
        return response

    def _failed(self, context, error, model_id, call_id):
        run_id = context.scope.identity.run_id
        correlation = {'run_id': run_id} if run_id else {}
        context_operations = context.scope.observation.context_operations
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
        return description


    async def awrap_model_call(self, request, handler):
        prepared = request.runtime.context.invocation.model_call.prepared
        if prepared is None:
            raise RuntimeError('Current model request is not prepared')
        model_id, call_id, model = prepared.model_id, prepared.model_call_id, prepared.model
        response = None
        try:
            configured = request.override(model=prepared.model,
                **({'tools': prepared.tools} if prepared.tools is not None else {}),
                **({'system_message': prepared.system_message} if prepared.system_message is not None else {}))
            control = request.runtime.context.scope.control
            if control is not None and native_async_model(model):
                response = await control.await_model(lambda: handler(configured))
            else:
                response = await handler(configured)
            return self._accept(request, response, model_id, call_id)
        except (Exception, asyncio.CancelledError, UserStop) as error:
            self._failed(request.runtime.context, error, model_id, call_id)
            raise


def event_label(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', value) else None


def token_count(value):
    return value if type(value) is int and value >= 0 else None


def _emit(context, event_type, data, *, primary_error=None):
    publisher = context.scope.observation.publisher if isinstance(context, RuntimeContext) else None
    if (not isinstance(publisher, (RunEventPublisher, AgentEventPublisher))
            or not publisher.matches(context.scope.identity.session_id, context.scope.identity.run_id)):
        return
    emit_event(publisher, event_type, data)
