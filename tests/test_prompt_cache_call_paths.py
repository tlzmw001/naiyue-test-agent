"""Real adapters inherit constructor cache defaults through existing callers."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import httpx2
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.model_access import load_model_catalog
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from test_auto_permission_review import verdict, settings as review_settings
from test_dynamic_tools import definition
from test_model_access import write_config
from test_subagent_execution import role


def completion_response(body, *, text='', call=None, usage=None):
    """Transport fixture only: production SDK serializes and parses every call."""
    content = {'role': 'assistant', 'content': text}
    if call:
        name, args, identity = call
        content['tool_calls'] = [{'id': identity, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}]
    finish = 'tool_calls' if call else 'stop'
    common = {'id': 'chatcmpl-' + uuid4().hex, 'created': 0, 'model': body['model']}
    usage = usage if usage is not None else {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}
    if body.get('stream'):
        delta = deepcopy(content)
        if call:
            delta['tool_calls'][0]['index'] = 0
        parts = [{**common, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta,
                  'finish_reason': None}]}, {**common, 'object': 'chat.completion.chunk',
                  'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}], 'usage': usage}]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=''.join('data: ' + json.dumps(part) + '\n\n' for part in parts) + 'data: [DONE]\n\n')
    return httpx.Response(200, json={**common, 'object': 'chat.completion', 'usage': usage,
        'choices': [{'index': 0, 'message': content, 'finish_reason': finish}]})


@asynccontextmanager
async def cached_application(monkeypatch, tmp_path, serve, *, tools=(), settings=None):
    config = {'schema_version': 1, 'default_model_id': 'root',
        'providers': {'gateway': {'kind': 'openai_compatible', 'base_url': 'https://example.invalid/v1',
                                  'api_key': 'offline-placeholder'}}, 'models': {}}
    for name, selected in (('root', 'in_memory'), ('child', '24h'), ('fast', 'in_memory'), ('detail', '24h')):
        config['models'][name] = {'provider_id': 'gateway', 'model': name, 'api': 'chat_completions',
            'profile': {'prompt_cache': {'protocol': 'openai', 'retentions': [selected]}},
            'request': {'sdk_max_retries': 0}, 'compaction_keep_recent_tokens': 64,
            'max_output_tokens': 1024, 'compaction_reserve_tokens': 2048,
            'compaction_summary_output_tokens': 256}
    config['models']['root']['thinking'] = {'supported_levels': ['low'], 'default_level': 'model_default'}
    catalog = load_model_catalog(write_config(tmp_path, config))
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
    app = await build_async_local_application('unused', tools=list(tools), settings=settings or AppSettings(),
        initial_active_tools=[spec.tool.name for spec in tools], system_prompt='ROOT', response_format=None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as transport:
        for name in catalog.model_ids:
            model = catalog.get(name)
            model.root_async_client = model.root_async_client.with_options(http_client=transport)
            model.async_client = model.root_async_client.chat.completions
        try:
            yield app, catalog
        finally:
            await app.aclose()


@async_test
async def test_root_child_summary_and_auto_use_own_model_defaults(monkeypatch, tmp_path, caplog):
    sent, effects = [], []
    def serve(request):
        body = json.loads(request.content)
        sent.append(deepcopy(body))
        model, messages = body['model'], body['messages']
        if model in ('fast', 'detail'):
            return completion_response(body, text=verdict('ask' if model == 'fast' else 'allow').content)
        if messages[0]['content'].startswith('Summarize completed conversation history'):
            return completion_response(body, text='Earlier work completed with the delegated report.')
        if model == 'child':
            if any(m['role'] == 'tool' for m in messages):
                return completion_response(body, text='child report')
            return completion_response(body, call=('probe', {'value': 'child'}, 'child-probe'))
        humans = [m for m in messages if m['role'] == 'user']
        if len(humans) > 1:
            return completion_response(body, text='recent answer')
        results = [m for m in messages if m['role'] == 'tool']
        if not results:
            return completion_response(body, call=('probe', {'value': 'parent'}, 'parent-probe'))
        if len(results) == 1:
            return completion_response(body, call=('subagent', {'agent': 'tester', 'task': 'CHECK'}, 'delegate'))
        return completion_response(body, text='older details ' * 500)
    options = AppSettings(permissions=review_settings().permissions, subagents=(role(model_id='child'),))
    async with cached_application(monkeypatch, tmp_path, serve, settings=options,
                                  tools=[definition(calls=effects, approved=True), create_subagent_tool()]) as (app, catalog):
        defaults = {name: deepcopy(catalog.get(name).model_kwargs) for name in catalog.model_ids}
        sid = await app.acreate_session('root')
        await app.aselect_thinking(sid, 'root', 'low')
        result = await app.arun(sid, [('user', 'old task')])
        assert result['messages'][-1].content == 'older details ' * 500
        assert effects == [('probe', 'parent'), ('probe', 'child')]
        assert [(m.tool_call_id, m.status) for m in result['messages'] if isinstance(m, ToolMessage)] == [
            ('parent-probe', 'success'), ('delegate', 'success')]
        await app.arun(sid, [('user', 'recent task')])
        before = len(sent)
        compacted = await app.acompact_context(sid)
        assert compacted['compaction_status'] == 'applied' and len(sent) == before + 1
        assert sent[-1]['max_completion_tokens'] == 256
        assert sent[-1]['messages'][0]['content'].startswith('Summarize completed conversation history')
        assert {name: catalog.get(name).model_kwargs for name in catalog.model_ids} == defaults
        assert catalog.get('root').reasoning_effort is None
        assert [body['model'] for body in sent].count('root') == 5
        assert [body['model'] for body in sent].count('child') == 2
        assert [body['model'] for body in sent].count('fast') == 2
        assert [body['model'] for body in sent].count('detail') == 2
        assert all(body['reasoning_effort'] == 'low' for body in sent if body['model'] == 'root')
        assert all(body['prompt_cache_retention'] == ('in_memory' if body['model'] in ('root', 'fast') else '24h')
                   for body in sent)
        assert not any('prompt_cache_key' in body for body in sent)
        assert all(body.get('tools') == [] or 'tools' not in body for body in sent if body['model'] in ('fast', 'detail'))
    assert not any(word in caplog.text for word in ('observation_failed', 'run_event_publish_failed'))


@async_test
async def test_shared_model_defaults_survive_concurrent_calls_and_endpoint_failure(monkeypatch, tmp_path, caplog):
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []
    async def serve(request):
        body = json.loads(request.content)
        sent.append(deepcopy(body))
        human = next(m['content'] for m in body['messages'] if m['role'] == 'user')
        if human == 'hold':
            entered.set()
            await release.wait()
        if human == 'fail':
            return httpx.Response(400, json={'error': {'message': 'PRIVATE_ENDPOINT_DETAIL',
                                                      'code': 'unsupported_parameter', 'type': 'invalid_request_error'}})
        return completion_response(body, text='done ' + human)
    async with cached_application(monkeypatch, tmp_path, serve) as (app, catalog):
        a, b, c = [await app.acreate_session('root') for _ in range(3)]
        before = deepcopy(catalog.get('root').model_kwargs)
        task = asyncio.create_task(app.arun(a, [('user', 'hold')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert (await app.arun(b, [('user', 'healthy')]))['messages'][-1].content == 'done healthy'
            import openai
            with pytest.raises(openai.BadRequestError):
                await app.arun(c, [('user', 'fail')])
            assert not task.done() and len(sent) == 3
            release.set()
            assert (await task)['messages'][-1].content == 'done hold'
            assert all(body['prompt_cache_retention'] == 'in_memory' for body in sent)
            assert catalog.get('root').model_kwargs == before
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    assert 'PRIVATE_ENDPOINT_DETAIL' not in caplog.text


@async_test
async def test_anthropic_ainvoke_and_output_binding_preserve_cache_control(tmp_path):
    sent = []
    config = {'schema_version': 1, 'default_model_id': 'claude',
        'providers': {'claude': {'kind': 'anthropic', 'api_key': 'offline-placeholder'}},
        'models': {'claude': {'provider_id': 'claude', 'model': 'synthetic', 'max_output_tokens': 128,
            'profile': {'prompt_cache': {'protocol': 'anthropic', 'retentions': ['1h', '5m']}},
            'request': {'sdk_max_retries': 0}}}}
    catalog = load_model_catalog(write_config(tmp_path, config))
    def serve(request):
        body = json.loads(request.content)
        sent.append(body)
        return httpx2.Response(200, json={'id': 'msg_offline', 'type': 'message', 'role': 'assistant',
            'model': 'synthetic', 'content': [{'type': 'text', 'text': 'done'}], 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 10, 'output_tokens': 2}})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(serve)) as transport:
        model = catalog.get('claude')
        original = deepcopy(model.model_kwargs)
        model.__dict__['_async_client'] = model._async_client.with_options(http_client=transport)
        for selected in (model, model.bind(max_tokens=32)):
            assert (await selected.ainvoke([HumanMessage(content='offline')])).content == 'done'
        assert [body['max_tokens'] for body in sent] == [128, 32]
        assert all(body['cache_control'] == {'type': 'ephemeral', 'ttl': '5m'} for body in sent)
        assert model.model_kwargs == original
    await catalog.aclose()


@pytest.mark.parametrize('protocol,retentions,expected', [
    ('openai', ['24h', 'in_memory'], {'prompt_cache_retention': 'in_memory'}),
    ('openai_options', ['30m'], {'prompt_cache_options': {'mode': 'implicit', 'ttl': '30m'}}),
])
@async_test
async def test_responses_sdk_stream_and_tool_binding_keep_constructor_defaults(tmp_path, protocol, retentions, expected):
    from test_model_access import config_for_all_kinds
    from test_responses_final_arguments import sequence
    config = config_for_all_kinds()
    config['models']['gateway'].update(api='responses', profile={
        'prompt_cache': {'protocol': protocol, 'retentions': retentions}}, request={'sdk_max_retries': 0})
    catalog = load_model_catalog(write_config(tmp_path, config))
    catalog.prepare_async_clients()
    sent = []
    def serve(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=''.join('data: ' + json.dumps(event) + '\n\n' for event in sequence('final_only')))
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as transport:
            model = catalog.get('gateway')
            model.root_async_client = model.root_async_client.with_options(http_client=transport)
            original = deepcopy(model.model_kwargs), deepcopy(model.prompt_cache_options)
            bound = model.bind_tools([{'type': 'function', 'function': {'name': 'read',
                'description': 'Read a file', 'parameters': {'type': 'object', 'properties': {
                    'path': {'type': 'string'}}, 'required': ['path']}}}])
            source = HumanMessage(content='offline input')
            chunks = [chunk async for chunk in bound.astream([source])]
            combined = chunks[0]
            for chunk in chunks[1:]:
                combined += chunk
            assert [call['args'] for call in combined.tool_calls] == [{'path': 'left'}, {'path': 'right'}]
            assert source.content == 'offline input' and not source.additional_kwargs
            body, = sent
            assert {key: body[key] for key in expected} == expected
            assert body['stream'] is True and body['tools'][0]['name'] == 'read'
            assert (model.model_kwargs, model.prompt_cache_options) == original
    finally:
        await catalog.aclose()
