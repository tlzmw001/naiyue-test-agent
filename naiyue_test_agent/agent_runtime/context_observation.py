"""Safe, per-call observations of framework-side model inputs."""

import hashlib
import json
import logging
from collections import Counter

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import BaseTool

from naiyue_test_agent.model_access import (
    COMPACTION_RESERVE_METADATA_KEY,
    MODEL_LIMITS_METADATA_KEY,
)
from naiyue_test_agent.model_access.messages import is_image_block


logger = logging.getLogger(__name__)


def _text_chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(block['text']) for block in content
                   if isinstance(block, dict) and isinstance(block.get('text'), str))
    return 0


def _tool_schema_summary(tools) -> tuple[str, int | None, str | None]:
    try:
        schemas = []
        for item in tools:
            if isinstance(item, BaseTool):
                schemas.append({
                    'name': item.name,
                    'description': item.description,
                    'parameters': item.tool_call_schema.model_json_schema(),
                })
            elif isinstance(item, dict):
                schemas.append(item)
            else:
                raise TypeError('unsupported_tool_definition')
        encoded = json.dumps(schemas, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
    except (AttributeError, TypeError, ValueError) as error:
        logger.warning('model_request_tool_schema_unavailable error_type=%s',
                       type(error).__name__)
        return 'unavailable', None, None
    return 'available', len(encoded), hashlib.sha256(encoded).hexdigest()


def _prepare_observation(request):
    """Build per-call diagnostic data; never retain it on the shared middleware."""
    context = request.runtime.context or {}
    messages = request.messages
    schema_status, schema_bytes, schema_hash = _tool_schema_summary(request.tools)
    payload = {
        'event': 'model_request_context',
        'session_id': context.get('session_id'),
        'run_id': context.get('run_id'),
        'message_count': len(messages),
        'message_types': dict(Counter(message.type for message in messages)),
        'system_text_chars': (_text_chars(request.system_message.content)
                              if request.system_message else 0),
        'message_text_chars': sum(_text_chars(message.content) for message in messages),
        'tool_call_count': sum(len(message.tool_calls) for message in messages
                               if isinstance(message, AIMessage)),
        'tool_count': len(request.tools),
        'schema_status': schema_status,
        'tool_schema_bytes': schema_bytes,
        'tool_schema_sha256': schema_hash,
        'input_tokens': None,
        'outcome': 'failed',
    }
    images = sum(is_image_block(block) for message in messages
                 if isinstance(message.content, list) for block in message.content)
    if images:
        payload.update(image_count=images, image_token_estimate='framework_approximate')
    model_metadata = getattr(getattr(request, 'model', None), 'metadata', None)
    limits = (model_metadata.get(MODEL_LIMITS_METADATA_KEY)
              if isinstance(model_metadata, dict) else None)
    reserve = (model_metadata.get(COMPACTION_RESERVE_METADATA_KEY)
               if isinstance(model_metadata, dict) else None)
    budget = None
    if isinstance(limits, dict):
        window = limits.get('context_window_tokens')
        output = limits.get('max_output_tokens')
        if type(window) is int and window > 0 and type(output) is int and output > 0:
            budget = {
                'event': 'model_context_budget',
                'model_id': limits.get('model_id'),
                'session_id': context.get('session_id'),
                'run_id': context.get('run_id'),
                'context_window_tokens': window,
                'max_output_tokens': output,
                'input_budget_tokens': max(0, window - output),
                'input_tokens': None,
                'threshold_exceeded': None,
                'context_window_source': limits.get('context_window_source'),
                'max_output_source': limits.get('max_output_source'),
                'outcome': 'failed',
            }
            if isinstance(reserve, dict) and type(reserve.get('tokens')) is int:
                headroom = max(output, reserve['tokens'])
                threshold = max(0, window - headroom)
                preflight = {
                    'event': 'model_context_preflight',
                    'model_id': limits.get('model_id'),
                    'session_id': context.get('session_id'),
                    'run_id': context.get('run_id'),
                    'context_window_tokens': window,
                    'context_window_source': limits.get('context_window_source'),
                    'max_output_tokens': output,
                    'compaction_reserve_tokens': reserve['tokens'],
                    'compaction_reserve_source': reserve.get('source'),
                    'effective_headroom_tokens': headroom,
                    'trigger_threshold_tokens': threshold,
                    'estimated_input_tokens': None,
                    'needs_compaction': None,
                    'estimate_status': 'unavailable',
                }
                try:
                    prompt_messages = ([request.system_message] if request.system_message else [])
                    estimated = count_tokens_approximately(
                        [*prompt_messages, *messages], tools=request.tools,
                    )
                    preflight['estimated_input_tokens'] = estimated
                    preflight['needs_compaction'] = (headroom >= window
                                                     or estimated > threshold)
                    preflight['estimate_status'] = 'available'
                except Exception as error:
                    logger.warning('context_preflight_estimate_unavailable error_type=%s',
                                   type(error).__name__)
                logger.info('model_context_preflight',
                            extra={'model_context_preflight': preflight})
    return payload, budget


def _observe_returned(response, payload, budget):
    message = next((item for item in response.result if isinstance(item, AIMessage)), None)
    usage = message.usage_metadata if message is not None else None
    input_tokens = usage.get('input_tokens') if usage else None
    if type(input_tokens) is int and input_tokens >= 0:
        payload['input_tokens'] = input_tokens
        if budget is not None:
            budget['input_tokens'] = input_tokens
            budget['threshold_exceeded'] = input_tokens > budget['input_budget_tokens']
    payload['outcome'] = 'returned'
    if budget is not None:
        budget['outcome'] = 'returned'


def _finish_observation(payload, budget):
    logger.info('model_request_context', extra={'model_request_context': payload})
    if budget is not None:
        logger.info('model_context_budget', extra={'model_context_budget': budget})


class _ModelRequestObservationMiddleware(AgentMiddleware):
    @property
    def name(self):
        return 'observe_model_request'

    def wrap_model_call(self, request: ModelRequest, handler):
        payload, budget = _prepare_observation(request)
        try:
            response = handler(request)
            _observe_returned(response, payload, budget)
            return response
        finally:
            _finish_observation(payload, budget)

    async def awrap_model_call(self, request: ModelRequest, handler):
        payload, budget = _prepare_observation(request)
        try:
            response = await handler(request)
            _observe_returned(response, payload, budget)
            return response
        finally:
            _finish_observation(payload, budget)


observe_model_request = _ModelRequestObservationMiddleware()
