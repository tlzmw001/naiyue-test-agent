"""Public loader/SDK streaming regression: final parameters are execution data."""

import asyncio
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage

from naiyue_test_agent.model_access import load_model_catalog


def configured(tmp_path):
    path = tmp_path / 'models.json'
    path.write_text(json.dumps({'schema_version': 1, 'default_model_id': 'test',
        'providers': {'test': {'kind': 'openai_compatible', 'api_key': 'synthetic',
                               'base_url': 'https://example.invalid/v1'}},
        'models': {'test': {'provider_id': 'test', 'model': 'synthetic', 'api': 'responses'}}}))
    return load_model_catalog(path)


def tool_item(index, arguments):
    return {'id': f'fc_{index}', 'type': 'function_call', 'name': 'read',
            'call_id': f'call_{index}', 'status': 'completed', 'arguments': arguments}


def event(kind, index, **fields):
    return {'type': 'response.' + kind, 'output_index': index, 'sequence_number': 1, **fields}


def sequence(variant):
    args = [json.dumps({'path': 'left'}), json.dumps({'path': 'right'})]
    events = [event('output_item.added', i, item=tool_item(i, '')) for i in range(2)]
    if variant in ('delta', 'partial', 'reverse_done'):
        for i in (0, 1):
            events.append(event('function_call_arguments.delta', i, item_id=f'fc_{i}',
                                delta=args[i] if variant != 'partial' else args[i][:5]))
    if variant == 'arguments_done':
        events += [event('function_call_arguments.done', i, item_id=f'fc_{i}', arguments=args[i]) for i in range(2)]
    else:
        order = (1, 0) if variant == 'reverse_done' else range(2)
        events += [event('output_item.done', i, item=tool_item(i, args[i])) for i in order]
    response = {'id': 'resp_test', 'object': 'response', 'created_at': 1, 'model': 'synthetic',
        'status': 'completed', 'error': None, 'incomplete_details': None,
        'output': [tool_item(i, args[i]) for i in range(2)],
        'usage': {'input_tokens': 12, 'output_tokens': 5, 'total_tokens': 17,
                  'input_tokens_details': {}, 'output_tokens_details': {}}}
    events.append({'type': 'response.completed', 'sequence_number': 20, 'response': response})
    return events


@pytest.mark.parametrize('variant', ['final_only', 'delta', 'partial', 'arguments_done', 'reverse_done'])
@pytest.mark.parametrize('headers', [False, True])
def test_public_stream_preserves_final_arguments_and_usage(monkeypatch, tmp_path, variant, headers):
    requests = []
    async def transport(_, request):
        requests.append(json.loads(request.content))
        body = ''.join('data: ' + json.dumps(e) + '\n\n' for e in sequence(variant))
        return httpx.Response(200, headers={'content-type': 'text/event-stream', 'x-test': 'yes'}, content=body)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', transport)

    async def run():
        catalog = configured(tmp_path)
        catalog._prepare_async_clients()
        model = catalog.get('test')
        original = model.root_async_client
        model.include_response_headers = headers
        try:
            chunks = [chunk async for chunk in model.astream([HumanMessage(content='synthetic')])]
            joined = chunks[0]
            for chunk in chunks[1:]:
                joined += chunk
            assert [call['args'] for call in joined.tool_calls] == [{'path': 'left'}, {'path': 'right'}]
            assert [call['id'] for call in joined.tool_calls] == ['call_0', 'call_1']
            assert not joined.invalid_tool_calls
            assert joined.usage_metadata['input_tokens'] == 12
            assert model.root_async_client is original
            if headers:
                assert joined.response_metadata['headers']['x-test'] == 'yes'
            assert len(requests) == 1 and requests[0]['stream'] is True
        finally:
            await catalog.aclose()
    asyncio.run(run())
