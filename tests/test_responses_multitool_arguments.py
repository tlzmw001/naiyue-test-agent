"""Pinned Responses converter keeps two synthetic tool argument streams separate."""

import json
from types import SimpleNamespace as Event

from langchain_openai.chat_models.base import _convert_responses_chunk_to_generation_chunk


def test_two_native_tool_streams_keep_distinct_names_ids_and_arguments():
    state, joined = (-1, -1, -1), None
    expected = [{'agent': 'tester', 'task': 'left'}, {'agent': 'tester', 'task': 'right'}]
    for index, args in enumerate(expected):
        events = [Event(type='response.output_item.added', output_index=index,
            item=Event(type='function_call', name='subagent', arguments='', call_id=f'call-{index}', id=f'item-{index}')),
            Event(type='response.function_call_arguments.delta', output_index=index, delta=json.dumps(args))]
        for event in events:
            *state, chunk = _convert_responses_chunk_to_generation_chunk(event, *state)
            joined = chunk if joined is None else joined + chunk
    assert not joined.message.invalid_tool_calls
    assert [call['args'] for call in joined.message.tool_calls] == expected
    assert [call['id'] for call in joined.message.tool_calls] == ['call-0', 'call-1']
