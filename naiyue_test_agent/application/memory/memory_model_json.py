"""Strict raw JSON decoding shared by the two explicit model stages."""
import json
from langchain_core.messages import AIMessage
from naiyue_test_agent.model_access.stop_reason import normalize_stop_reason
from naiyue_test_agent.contracts.memory import MemoryReadError


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError('Non-finite JSON number')


def _content(response, output_mode, *, expected_name):
    if isinstance(response, AIMessage):
        stop = normalize_stop_reason(response)
        if stop == 'length':
            raise MemoryReadError('memory_result_truncated')
        if (stop == 'error' or response.invalid_tool_calls or response.additional_kwargs.get('refusal')
                or isinstance(response.content, list) and any(isinstance(b, dict) and b.get('type') == 'refusal'
                    for b in response.content)):
            raise MemoryReadError('memory_result_invalid')
        if output_mode == 'function_calling':
            if len(response.tool_calls) != 1 or response.tool_calls[0]['name'] != expected_name:
                raise MemoryReadError('memory_result_invalid')
            call = response.tool_calls[0]
            raw = response.additional_kwargs.get('tool_calls')
            if raw is not None:
                if (not isinstance(raw, list) or len(raw) != 1 or raw[0].get('id') != call['id']
                        or raw[0].get('function', {}).get('name') != expected_name):
                    raise MemoryReadError('memory_result_invalid')
                return raw[0]['function']['arguments']
            return json.dumps(call['args'], ensure_ascii=False, allow_nan=False)
        if stop == 'tool_use' or response.tool_calls:
            raise MemoryReadError('memory_result_invalid')
        content = response.content
        if isinstance(content, list):
            content = ''.join(b if isinstance(b, str) else b.get('text', '')
                if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str) else ''
                for b in content)
        return content
    if output_mode != 'text':
        raise MemoryReadError('memory_result_invalid')
    return response
