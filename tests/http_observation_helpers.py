"""Offline native SDK transports, shared by propagation and production tests."""

import json
from contextlib import asynccontextmanager

import httpx
import httpx2
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from naiyue_test_agent.model_access import ModelCatalog, MODEL_ID_METADATA_KEY
from naiyue_test_agent.model_access.responses_stream import ResponsesChatOpenAI
from test_prompt_cache_call_paths import completion_response
from test_responses_final_arguments import sequence


def sdk_response(request, kind):
    body = json.loads(request.content)
    if kind in ('openai', 'sync_fallback'):
        return completion_response(body, text='done')
    if kind == 'responses':
        rows = sequence('final_only')
        if body.get('stream'):
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                content=''.join('data: ' + json.dumps(row) + '\n\n' for row in rows))
        return httpx.Response(200, json=rows[-1]['response'])
    if kind == 'ollama':
        response = {'model': 'synthetic', 'created_at': '2026-01-01T00:00:00Z',
            'message': {'role': 'assistant', 'content': 'done'}, 'done': True,
            'done_reason': 'stop', 'prompt_eval_count': 10, 'eval_count': 2}
        return httpx.Response(200, content=json.dumps(response) + '\n')
    message = {'id': 'msg_offline', 'type': 'message', 'role': 'assistant', 'model': 'synthetic',
        'content': [{'type': 'text', 'text': 'done'}], 'stop_reason': 'end_turn',
        'stop_sequence': None, 'usage': {'input_tokens': 10, 'output_tokens': 2}}
    if not body.get('stream'):
        return httpx2.Response(200, json=message)
    rows = [
        {'type': 'message_start', 'message': {**message, 'content': [], 'stop_reason': None}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'done'}},
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
         'usage': {'output_tokens': 2}}, {'type': 'message_stop'},
    ]
    return httpx2.Response(200, headers={'content-type': 'text/event-stream'},
        content=''.join('event: ' + row['type'] + '\ndata: ' + json.dumps(row) + '\n\n' for row in rows))


@asynccontextmanager
async def sdk_catalog(kind='openai', *, serve=None):
    metadata = {MODEL_ID_METADATA_KEY: 'A'}
    if kind == 'anthropic':
        model = ChatAnthropic(model='synthetic', api_key='offline-placeholder', max_tokens=128, metadata=metadata)
    elif kind == 'ollama':
        model = ChatOllama(model='synthetic', base_url='https://example.invalid', metadata=metadata)
    else:
        cls = ResponsesChatOpenAI if kind == 'responses' else ChatOpenAI
        if kind == 'sync_fallback':
            from langchain_core.language_models import BaseChatModel
            class SyncFallback(ChatOpenAI):
                _agenerate = BaseChatModel._agenerate
                _astream = BaseChatModel._astream
            cls = SyncFallback
        model = cls(model='synthetic', api_key='offline-placeholder', base_url='https://example.invalid/v1',
                    metadata=metadata, use_responses_api=kind == 'responses')
    catalog = ModelCatalog('A', {'A': model})
    catalog.prepare_async_clients()
    try:
        for client, _ in catalog._owned_http_clients:
            module = httpx2 if kind == 'anthropic' else httpx
            # Preserve the production-owned client's hooks/options/close path.
            client._transport = module.MockTransport(serve or (lambda request: sdk_response(request, kind)))
            client._mounts = {}
        yield catalog
    finally:
        await catalog.aclose()
