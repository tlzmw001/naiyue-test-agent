"""Provider metadata becomes one project-level model stop reason."""

import pytest

from langchain_core.messages import AIMessage

from naiyue_test_agent.model_access.stop_reason import normalize_stop_reason


@pytest.mark.parametrize(('metadata', 'tool_calls', 'expected'), [
    ({'finish_reason': 'stop'}, False, 'stop'),
    ({'finish_reason': 'tool_calls'}, True, 'tool_use'),
    ({'finish_reason': 'stop'}, True, 'tool_use'),
    ({'finish_reason': 'length'}, True, 'length'),
    ({'finish_reason': 'content_filter'}, False, 'error'),
    ({'status': 'completed'}, False, 'stop'),
    ({'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}},
     True, 'length'),
    ({'status': 'incomplete', 'incomplete_details': {'reason': 'content_filter'}},
     False, 'error'),
    ({'status': 'failed'}, False, 'error'),
    ({'stop_reason': 'end_turn'}, False, 'stop'),
    ({'stop_reason': 'tool_use'}, True, 'tool_use'),
    ({'stop_reason': 'max_tokens'}, True, 'length'),
    ({'stop_reason': 'model_context_window_exceeded'}, True, 'length'),
    ({'stop_reason': 'refusal'}, False, 'error'),
    ({'done_reason': 'stop'}, False, 'stop'),
    ({'done_reason': 'length'}, True, 'length'),
    ({'done_reason': 'load'}, False, 'unknown'),
    ({'finish_reason': 'future_reason'}, False, 'unknown'),
    ({}, False, 'unknown'),
])
def test_normalization_preserves_provider_metadata(metadata, tool_calls, expected):
    calls = [{'name': 'probe', 'args': {}, 'id': 'call-1'}] if tool_calls else []
    message = AIMessage(content='', tool_calls=calls, response_metadata=metadata.copy())

    assert normalize_stop_reason(message) == expected
    assert message.response_metadata == metadata
