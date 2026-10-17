"""Inspect real installed adapter payloads without a network request."""

import base64
import json

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from naiyue_test_agent.model_access.messages import prepare_model_messages
from test_read_images import png_bytes


def history():
    image = {'type': 'image', 'mime_type': 'image/png', 'base64': base64.b64encode(png_bytes()).decode()}
    return [HumanMessage(content='inspect'), AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': 'image.png'}, 'id': 'i'},
        {'name': 'read', 'args': {'path': 'text.txt'}, 'id': 't'}]),
        ToolMessage(tool_call_id='i', content=[{'type': 'text', 'text': 'image'}, image]),
        ToolMessage(tool_call_id='t', content='other text')]


def openai_model(responses=False, vision=True):
    return ChatOpenAI(model='synthetic', api_key='synthetic', use_responses_api=responses,
                      profile={'image_inputs': vision})


def test_chat_completions_carrier_follows_complete_batch_and_preserves_history():
    original = history()
    before = [m.model_dump() for m in original]
    model = openai_model()
    prepared = prepare_model_messages(original, model)
    assert [m.type for m in prepared] == ['human', 'ai', 'tool', 'tool', 'human']
    assert prepared[-1].additional_kwargs == {'naiyue_image_carrier': True}
    assert prepared[-1].content[0]['text'].endswith('"i":')
    payload = model._get_request_payload(prepared)
    assert payload['messages'][-1]['role'] == 'user'
    assert payload['messages'][-1]['content'][-1]['type'] == 'image_url'
    assert 'image_url' not in json.dumps(payload['messages'][2:4])
    assert [m.model_dump() for m in original] == before
    assert [m.model_dump() for m in prepare_model_messages(original, model)] == [m.model_dump() for m in prepared]


@pytest.mark.parametrize('provider', ['responses', 'anthropic', 'ollama'])
def test_native_adapters_keep_image_in_tool_result(provider):
    messages = history()
    if provider == 'responses':
        model = openai_model(responses=True)
        payload = model._get_request_payload(prepare_model_messages(messages, model))
        output = next(item['output'] for item in payload['input'] if item['type'] == 'function_call_output' and item['call_id'] == 'i')
        assert output[-1]['type'] == 'input_image' and output[-1]['image_url'].startswith('data:image/png;base64,')
    elif provider == 'anthropic':
        model = ChatAnthropic(model='synthetic', api_key='synthetic', profile={'image_inputs': True})
        payload = model._get_request_payload(prepare_model_messages(messages, model))
        result = next(block for row in payload['messages'] for block in row['content']
                      if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('tool_use_id') == 'i')
        assert result['content'][-1]['type'] == 'image'
        assert result['content'][-1]['source']['media_type'] == 'image/png'
    else:
        model = ChatOllama(model='synthetic', profile={'image_inputs': True})
        payload = model._convert_messages_to_ollama_messages(prepare_model_messages(messages, model))
        assert payload[2]['role'] == 'tool' and payload[2]['images']
    assert len(prepare_model_messages(messages, model)) == len(messages)


def test_model_switch_preserves_canonical_pixels_and_unsupported_notice():
    original = history()
    pixels = original[2].content[-1]['base64']
    text = prepare_model_messages(original, openai_model(vision=False))
    assert 'does not support image' in json.dumps([m.content for m in text])
    assert pixels not in json.dumps([m.content for m in text])
    assert original[2].content[-1]['base64'] == pixels
    assert prepare_model_messages(original, openai_model())[-1].content[-1]['base64'] == pixels


def test_image_estimate_does_not_count_base64_as_text():
    messages = history()
    small = count_tokens_approximately(messages)
    messages[2].content[-1]['base64'] = 'A' * 1_000_000
    assert count_tokens_approximately(messages) == small


def test_incomplete_image_batch_cannot_send_carrier_before_missing_result():
    with pytest.raises(ValueError, match='incomplete'):
        prepare_model_messages(history()[:-1], openai_model())


def test_multiple_images_share_one_carrier_per_complete_tool_batch():
    original = history()
    original[3].content = list(original[2].content)
    original.extend([AIMessage(content='', tool_calls=[{'name': 'read', 'args': {}, 'id': 'j'}]),
                     ToolMessage(tool_call_id='j', content=list(original[2].content))])
    prepared = prepare_model_messages(original, openai_model())
    carriers = [m for m in prepared if m.additional_kwargs.get('naiyue_image_carrier')]
    assert len(carriers) == 2
    assert [sum(b.get('type') == 'image' for b in m.content) for m in carriers] == [2, 1]
    assert [i for i, m in enumerate(prepared) if m.additional_kwargs.get('naiyue_image_carrier')] == [4, 7]
    assert len(original) == 6 and all(not m.additional_kwargs.get('naiyue_image_carrier') for m in original)
