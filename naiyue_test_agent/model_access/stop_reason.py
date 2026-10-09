"""Normalize provider stop metadata already adapted into a LangChain AIMessage."""

import re
from typing import Literal

from langchain_core.messages import AIMessage


MODEL_STOP_REASON_KEY = 'naiyue_stop_reason'
StopReason = Literal['stop', 'tool_use', 'length', 'error', 'unknown']
_SAFE_NATIVE_REASON = re.compile(r'[a-z][a-z_]{0,31}\Z')
_SENSITIVE_REASON_WORDS = ('secret', 'token', 'password', 'credential', 'api_key',
                           'apikey', 'auth', 'bearer', 'private')
_SENSITIVE_REASON_PREFIXES = ('sk_', 'ghp_', 'pat_', 'xoxb_')


def normalize_stop_reason(message: AIMessage) -> StopReason:
    """Keep native metadata intact while interpreting known completion reasons."""
    metadata = message.response_metadata
    reason: StopReason = 'unknown'

    if 'status' in metadata:
        status = metadata['status']
        if status == 'completed':
            reason = 'stop'
        elif status == 'incomplete':
            details = metadata.get('incomplete_details')
            reason = ('length' if isinstance(details, dict)
                      and details.get('reason') == 'max_output_tokens' else 'error')
        elif status in ('failed', 'cancelled'):
            reason = 'error'
    elif 'finish_reason' in metadata:
        native = metadata['finish_reason']
        if native in ('stop', 'end'):
            reason = 'stop'
        elif native in ('tool_calls', 'function_call'):
            reason = 'tool_use'
        elif native == 'length':
            reason = 'length'
        elif native in ('content_filter', 'network_error'):
            reason = 'error'
    elif 'stop_reason' in metadata:
        native = metadata['stop_reason']
        if native in ('end_turn', 'pause_turn', 'stop_sequence'):
            reason = 'stop'
        elif native == 'tool_use':
            reason = 'tool_use'
        elif native in ('max_tokens', 'model_context_window_exceeded'):
            reason = 'length'
        elif native in ('refusal', 'sensitive'):
            reason = 'error'
    elif 'done_reason' in metadata:
        native = metadata['done_reason']
        if native == 'stop':
            reason = 'stop'
        elif native == 'length':
            reason = 'length'

    if reason == 'stop' and message.tool_calls:
        return 'tool_use'
    return reason


def unknown_stop_reason_diagnostics(message: AIMessage) -> dict[str, str | bool | None]:
    """Describe an unknown native stop value without logging arbitrary metadata."""
    metadata = message.response_metadata
    for field in ('status', 'finish_reason', 'stop_reason', 'done_reason'):
        if field in metadata:
            native = metadata[field]
            safe = (isinstance(native, str) and _SAFE_NATIVE_REASON.fullmatch(native)
                    and not native.startswith(_SENSITIVE_REASON_PREFIXES)
                    and not any(word in native for word in _SENSITIVE_REASON_WORDS))
            return {
                'reason_source': field,
                'native_reason_code': native if safe else None,
                'native_reason_redacted': not safe,
            }
    return {
        'reason_source': 'missing',
        'native_reason_code': None,
        'native_reason_redacted': False,
    }
