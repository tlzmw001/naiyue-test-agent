"""Recover one explicitly rejected model request without replaying the agent."""

from .observation import observe

import asyncio
import logging

from langchain.agents.middleware import AgentMiddleware, ExtendedModelResponse
from langgraph.types import Command

from naiyue_test_agent.agent_runtime.context_compaction import ContextCompactionError, _acompact_messages
from naiyue_test_agent.model_access import MODEL_LIMITS_METADATA_KEY
from naiyue_test_agent.model_access.errors import input_context_overflow_reason


logger = logging.getLogger(__name__)


def _begin_recovery(request, reason):
    # ModelInputMiddleware is inside this wrapper: request still contains canonical
    # blocks, and each handler(retry) builds a fresh provider-specific copy.
    context = request.runtime.context
    metadata = getattr(request.model, 'metadata', None) or {}
    limits = metadata.get(MODEL_LIMITS_METADATA_KEY, {})
    correlation = {
        'session_id': context.scope.identity.session_id, 'run_id': context.scope.identity.run_id,
        'model_id': limits.get('model_id'),
    }
    logger.warning('context_overflow_detected', extra={'context_recovery': {
        'event': 'context_overflow_detected', **correlation, 'reason': reason,
    }})
    observer = context.scope.observation.context_operations
    operation_id = observe(observer, observer.begin_recovery, limits.get('model_id')) if observer is not None else None
    arguments = dict(
        messages=request.messages, model=request.model, model_id=limits.get('model_id'),
        context=context, system_messages=[request.system_message] if request.system_message else [],
        tools=request.tools, force=True,
    )
    return arguments, correlation, observer, operation_id


def _retry_request(request, update, correlation, observer, operation_id):
    # REMOVE_ALL_MESSAGES belongs to state, never to the provider input.
    retry_messages = update['messages'][1:]
    logger.info('context_overflow_retry_started', extra={'context_recovery': {
        'event': 'context_overflow_retry_started', **correlation,
    }})
    if observer is not None:
        observe(observer, observer.retry_started, operation_id)
    return request.override(messages=retry_messages)


def _recovery_failed(error, stage, correlation, observer, operation_id):
    logger.error('context_overflow_recovery_failed', extra={'context_recovery': {
        'event': 'context_overflow_recovery_failed', **correlation,
        'stage': stage, 'error_type': type(error).__name__,
    }})
    if observer is not None:
        observe(observer, observer.fail_recovery, operation_id, stage, error)
        if isinstance(error, asyncio.CancelledError):
            observe(observer, observer.flush)


def _recovery_returned(response, update, correlation, observer, operation_id):
    logger.info('context_overflow_retry_returned', extra={'context_recovery': {
        'event': 'context_overflow_retry_returned', **correlation,
    }})
    if observer is not None:
        observe(observer, observer.retry_returned, operation_id, response)
    # Re-add response IDs after clearing state; framework applies them before commands.
    return ExtendedModelResponse(
        model_response=response,
        command=Command(update={'messages': [*update['messages'], *response.result]}),
    )


class _InputOverflowRecoveryMiddleware(AgentMiddleware):
    @property
    def name(self):
        return 'recover_input_overflow'


    async def awrap_model_call(self, request, handler):
        try:
            return await handler(request)
        except Exception as error:
            reason = input_context_overflow_reason(error)
            if reason is None:
                raise

        arguments, correlation, observer, operation_id = _begin_recovery(request, reason)
        stage = 'compact'
        try:
            update = await _acompact_messages(**arguments)
            if update is None:
                raise ContextCompactionError('compaction_unavailable')
            stage = 'retry'
            retry = _retry_request(request, update, correlation, observer, operation_id)
            response = await handler(retry)
        except (Exception, asyncio.CancelledError) as error:
            _recovery_failed(error, stage, correlation, observer, operation_id)
            raise
        return _recovery_returned(response, update, correlation, observer, operation_id)


recover_input_overflow = _InputOverflowRecoveryMiddleware()
