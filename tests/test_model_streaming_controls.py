"""Per-model streaming controls reach the SDK without changing other models."""

import asyncio
import json

import httpx
import pytest

from naiyue_test_agent.model_access import (
    ModelConfigError, bind_model_output_limit, load_model_catalog,
)
from test_model_access import config_for_all_kinds, write_config


def test_omission_preserves_sdk_env_default_and_does_not_send_extensions(tmp_path, monkeypatch):
    monkeypatch.setenv('LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S', '240')
    catalog = load_model_catalog(write_config(tmp_path, config_for_all_kinds()))
    for model_id in ('official', 'gateway'):
        model = catalog.get(model_id)
        assert model.stream_chunk_timeout == 240
        payload = model._get_request_payload('synthetic')
        assert 'tool_stream' not in payload.get('extra_body', {})
        assert 'stream_chunk_timeout' not in payload


@pytest.mark.parametrize('enabled', [True, False])
def test_tool_stream_is_explicit_and_preserved_by_thinking_and_output_limit(tmp_path, enabled):
    config = config_for_all_kinds()
    entry = config['models']['gateway']
    entry['request'] = {'tool_stream': enabled, 'stream_chunk_timeout_seconds': 600,
                        'timeout_seconds': 300}
    entry['thinking'] = {'supported_levels': ['low', 'high'], 'default_level': 'low'}
    catalog = load_model_catalog(write_config(tmp_path, config))
    selected = catalog.with_thinking('gateway', 'high')
    for model in (catalog.get('gateway'), selected):
        assert model.extra_body == {'tool_stream': enabled}
        assert model.stream_chunk_timeout == 600
        assert model.request_timeout == 300
    assert selected.reasoning_effort == 'high'
    assert catalog.get('gateway').reasoning_effort == 'low'
    bound = bind_model_output_limit(selected, 128)
    assert bound.bound.extra_body == {'tool_stream': enabled}
    assert bound.bound.stream_chunk_timeout == 600
    assert bound.kwargs['max_completion_tokens'] == 128
    assert catalog.get('official').extra_body is None


@pytest.mark.parametrize('model_id', ['official', 'gateway'])
@pytest.mark.parametrize('api', ['chat_completions', 'responses'])
@pytest.mark.parametrize('timeout', [0, 1.5, 600])
def test_chunk_timeout_is_per_model_and_overrides_env(tmp_path, monkeypatch, model_id, api, timeout):
    monkeypatch.setenv('LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S', '240')
    config = config_for_all_kinds()
    config['models'][model_id].update(api=api, request={'stream_chunk_timeout_seconds': timeout})
    catalog = load_model_catalog(write_config(tmp_path, config))
    assert catalog.get(model_id).stream_chunk_timeout == timeout
    assert catalog.get(model_id).request_timeout == 60
    other = 'official' if model_id == 'gateway' else 'gateway'
    assert catalog.get(other).stream_chunk_timeout == 240


@pytest.mark.parametrize('field,value', [
    ('tool_stream', None), ('tool_stream', 0), ('tool_stream', 1),
    ('tool_stream', 'PRIVATE'), ('tool_stream', {}),
    ('stream_chunk_timeout_seconds', None), ('stream_chunk_timeout_seconds', True),
    ('stream_chunk_timeout_seconds', False), ('stream_chunk_timeout_seconds', -1),
    ('stream_chunk_timeout_seconds', 'PRIVATE'), ('stream_chunk_timeout_seconds', []),
    ('stream_chunk_timeout_seconds', float('nan')),
    ('stream_chunk_timeout_seconds', float('inf')),
    ('stream_chunk_timeout_seconds', -float('inf')),
])
def test_invalid_values_are_rejected_without_echoing_private_data(tmp_path, field, value):
    config = config_for_all_kinds()
    config['models']['gateway']['request'] = {field: value}
    with pytest.raises(ModelConfigError, match=field) as caught:
        load_model_catalog(write_config(tmp_path, config))
    assert 'PRIVATE' not in str(caught.value)
    assert 'gateway-test-key' not in str(caught.value)


@pytest.mark.parametrize('model_id,api,field,value', [
    ('official', 'chat_completions', 'tool_stream', True),
    ('gateway', 'responses', 'tool_stream', True),
    ('gateway', 'responses', 'tool_stream', False),
    ('claude', None, 'tool_stream', True),
    ('local', None, 'tool_stream', True),
    ('claude', None, 'stream_chunk_timeout_seconds', 600),
    ('local', None, 'stream_chunk_timeout_seconds', 600),
])
def test_unsupported_protocols_reject_instead_of_silently_ignoring(tmp_path, model_id, api, field, value):
    config = config_for_all_kinds()
    entry = config['models'][model_id]
    if api is not None:
        entry['api'] = api
    entry['request'] = {field: value}
    with pytest.raises(ModelConfigError, match=field):
        load_model_catalog(write_config(tmp_path, config))


@pytest.mark.parametrize('reasoning_output', [False, True])
@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('timeout', [0, 600])
def test_real_sdk_sends_tool_stream_and_uses_configured_wait(tmp_path, monkeypatch, reasoning_output, enabled, timeout):
    from langchain_openai.chat_models import _client_utils

    async def run():
        requests, waits = [], []
        original_wait = asyncio.wait_for

        async def observe_wait(awaitable, timeout):
            waits.append(timeout)
            return await original_wait(awaitable, timeout)

        async def transport(_, request):
            requests.append(json.loads(request.content))
            deltas = [
                {'role': 'assistant', 'content': 'writing'},
                {'tool_calls': [{'index': 0, 'id': 'call_1', 'type': 'function',
                                'function': {'name': 'write', 'arguments': '{"path":'}}]},
                {'tool_calls': [{'index': 0, 'function': {'arguments': '"synthetic.py","content":"ok"}'}}]},
            ]
            rows = [{'id': 'chat_1', 'object': 'chat.completion.chunk', 'model': 'synthetic',
                     'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
                    for delta in deltas]
            rows.append({'id': 'chat_1', 'object': 'chat.completion.chunk', 'model': 'synthetic',
                         'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]})
            body = ''.join('data: ' + json.dumps(row) + '\n\n' for row in rows) + 'data: [DONE]\n\n'
            return httpx.Response(200, content=body, headers={'content-type': 'text/event-stream'})

        monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', transport)
        monkeypatch.setattr(_client_utils.asyncio, 'wait_for', observe_wait)
        config = config_for_all_kinds()
        entry = config['models']['gateway']
        entry['request'] = {'tool_stream': enabled, 'stream_chunk_timeout_seconds': timeout,
                            'timeout_seconds': 300, 'sdk_max_retries': 0}
        if reasoning_output:
            entry['profile'] = {'reasoning_output': 'reasoning_content'}
        catalog = load_model_catalog(write_config(tmp_path, config))
        catalog.prepare_async_clients()
        try:
            parts = [part async for part in catalog.get('gateway').astream('synthetic')]
            full = parts[0]
            for part in parts[1:]:
                full += part
            assert full.tool_calls == [{'name': 'write', 'args': {'path': 'synthetic.py', 'content': 'ok'},
                                        'id': 'call_1', 'type': 'tool_call'}]
            assert len(requests) == 1
            assert requests[0]['tool_stream'] is enabled
            assert requests[0]['stream'] is True
            assert 'stream_chunk_timeout' not in requests[0]
            assert 'stream_chunk_timeout_seconds' not in requests[0]
            assert waits == ([600] * 5 if timeout else [])
        finally:
            await catalog.aclose()

    asyncio.run(run())


def test_configured_timeout_propagates_and_closes_http_without_real_delay(tmp_path, monkeypatch):
    from langchain_openai.chat_models import _client_utils
    from naiyue_test_agent.model_access.errors import classify_model_failure

    async def run():
        waits, closed = [], asyncio.Event()
        original_wait = asyncio.wait_for

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                row = {'id': 'chat_1', 'object': 'chat.completion.chunk', 'model': 'synthetic',
                       'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'start'},
                                    'finish_reason': None}]}
                yield ('data: ' + json.dumps(row) + '\n\n').encode()
                await asyncio.Event().wait()

            async def aclose(self):
                closed.set()

        async def transport(_, request):
            return httpx.Response(200, stream=Body(), headers={'content-type': 'text/event-stream'})

        async def expire_second_wait(awaitable, timeout):
            waits.append(timeout)
            # Deterministically expire the second read; no wall-clock race or sleep.
            return await original_wait(awaitable, timeout=0 if len(waits) == 2 else timeout)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', transport)
        monkeypatch.setattr(_client_utils.asyncio, 'wait_for', expire_second_wait)
        config = config_for_all_kinds()
        config['models']['gateway']['request'] = {'stream_chunk_timeout_seconds': 600,
                                                  'sdk_max_retries': 0}
        catalog = load_model_catalog(write_config(tmp_path, config))
        catalog.prepare_async_clients()
        try:
            stream = catalog.get('gateway').astream('synthetic')
            assert (await anext(stream)).text == 'start'
            with pytest.raises(_client_utils.StreamChunkTimeoutError) as caught:
                await anext(stream)
            assert waits == [600, 600]
            assert caught.value.timeout_s == 600
            assert caught.value.chunks_received == 1
            assert classify_model_failure(caught.value) == ('transient', 'timeout')
            assert closed.is_set()
        finally:
            await catalog.aclose()

    asyncio.run(run())
