"""The cache probe is async, bounded before send, and owns all its clients."""

import asyncio
import json
import httpx
import httpx2
import pytest

from native_application_helpers import async_test
from naiyue_test_agent.model_access import load_model_catalog
from scripts import probe_prompt_cache as probe
from scripts import provider_acceptance_budget as limits
from test_model_access import write_config
from test_prompt_cache_call_paths import completion_response


def setup_probe(monkeypatch, tmp_path, serve, *, capability=True, anthropic=False):
    config = {'schema_version': 1, 'default_model_id': 'main',
        'providers': {'gateway': {'kind': 'openai_compatible', 'base_url': 'https://example.invalid/v1',
                                  'api_key': 'offline-placeholder'}},
        'models': {'main': {'provider_id': 'gateway', 'model': 'synthetic', 'api': 'chat_completions', 'max_output_tokens': 1024,
            'profile': {'prompt_cache': {'protocol': 'openai', 'retentions': ['in_memory']}} if capability else {}}}}
    if anthropic:
        config['providers']['gateway'] = {'kind': 'anthropic', 'api_key': 'offline-placeholder'}
        config['models']['main'].pop('api')
        config['models']['main']['profile'] = {'prompt_cache': {'protocol': 'anthropic', 'retentions': ['5m']}}
    catalog = load_model_catalog(write_config(tmp_path, config))
    monkeypatch.setattr('naiyue_test_agent.model_access.load_model_catalog', lambda _: catalog)
    attempts = []
    async def send(transport, request):
        attempts.append(json.loads(request.content))
        return await serve(request, len(attempts))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', send)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, 'handle_async_request', send)
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    return catalog, attempts


def arguments():
    return ['--config', 'unused', '--model-id', 'main', '--deadline-seconds', '5',
            '--output-tokens', '64', '--prefix-repetitions', '120']


@async_test
async def test_probe_reports_usage_and_hit_without_printing_content(monkeypatch, tmp_path, capsys):
    async def serve(request, number):
        return completion_response(json.loads(request.content), text='PRIVATE_RESPONSE', usage={
            'prompt_tokens': 2000, 'completion_tokens': 2, 'prompt_tokens_details': {
                'cached_tokens': 1000 if number == 2 else 0, 'cache_write_tokens': 1000 if number == 1 else 0}})
    catalog, attempts = setup_probe(monkeypatch, tmp_path, serve)
    assert await probe.main(arguments()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'PASS' and result['requests'] == 2 and result['cache_hit'] is True
    assert [row['cache_write_tokens'] for row in result['usage']] == [1000, 0]
    assert [row['cache_read_tokens'] for row in result['usage']] == [0, 1000]
    assert len(attempts) == 2 and all(row['max_completion_tokens'] == 64 for row in attempts)
    assert all(row['prompt_cache_retention'] == 'in_memory' for row in attempts)
    assert all(row['store'] is False for row in attempts)
    assert all(client.is_closed for client, _ in catalog._owned_http_clients)
    assert 'PRIVATE' not in json.dumps(result)


@async_test
@pytest.mark.parametrize('kind', ['endpoint', 'unsupported', 'missing_usage'])
async def test_probe_stops_on_error_or_reports_unknown_without_guessing(monkeypatch, tmp_path, capsys, kind):
    async def serve(request, number):
        if kind == 'endpoint':
            return httpx.Response(500, json={'error': {'message': 'PRIVATE_PROVIDER_BODY'}})
        body = json.loads(request.content)
        response = completion_response(body, text='PRIVATE')
        value = response.json()
        value.pop('usage')
        return httpx.Response(200, json=value)
    catalog, attempts = setup_probe(monkeypatch, tmp_path, serve, capability=kind != 'unsupported')
    assert await probe.main(arguments()) == (0 if kind == 'missing_usage' else 1)
    result = json.loads(capsys.readouterr().out)
    assert result['requests'] == len(attempts) == (0 if kind == 'unsupported' else 1 if kind == 'endpoint' else 2)
    assert result['cache_hit'] is None
    assert all(client.is_closed for client, _ in catalog._owned_http_clients)
    assert 'PRIVATE' not in json.dumps(result)


@async_test
@pytest.mark.parametrize('kind', ['timeout', 'cancel'])
async def test_probe_wait_is_cancellable_and_closes_clients(monkeypatch, tmp_path, kind):
    entered, release = asyncio.Event(), asyncio.Event()
    async def serve(request, number):
        entered.set()
        await release.wait()
    catalog, attempts = setup_probe(monkeypatch, tmp_path, serve)
    deadlines = []
    real_timeout = asyncio.timeout
    def controlled_timeout(_):
        deadline = real_timeout(None)
        deadlines.append(deadline)
        return deadline
    monkeypatch.setattr(asyncio, 'timeout', controlled_timeout)
    task = asyncio.create_task(probe.main(arguments()))
    await asyncio.wait_for(entered.wait(), 5)
    if kind == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        deadlines[0].reschedule(asyncio.get_running_loop().time())
        assert await task == 1
    assert len(attempts) == 1
    assert all(client.is_closed for client, _ in catalog._owned_http_clients)


@async_test
async def test_probe_installs_existing_send_budget_before_binding(monkeypatch, tmp_path, capsys):
    async def serve(request, number):
        return completion_response(json.loads(request.content), text='OK')
    catalog, attempts = setup_probe(monkeypatch, tmp_path, serve)
    real_guard = limits.guard_catalog
    def one_send(cat, budget):
        real_guard(cat, budget.child(1))
    monkeypatch.setattr(limits, 'guard_catalog', one_send)
    assert await probe.main(arguments()) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['requests'] == len(attempts) == 1
    assert result['error_type'] == 'AcceptanceBudgetStop'
    assert all(client.is_closed for client, _ in catalog._owned_http_clients)


@async_test
async def test_anthropic_probe_uses_native_payload_and_split_write_usage(monkeypatch, tmp_path, capsys):
    async def serve(request, number):
        return httpx2.Response(200, json={'id': 'msg_offline', 'type': 'message', 'role': 'assistant',
            'model': 'synthetic', 'content': [{'type': 'text', 'text': 'PRIVATE'}], 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 10, 'output_tokens': 2, 'cache_read_input_tokens': 100,
                'cache_creation_input_tokens': 40, 'cache_creation': {
                    'ephemeral_5m_input_tokens': 30, 'ephemeral_1h_input_tokens': 10}}})
    catalog, attempts = setup_probe(monkeypatch, tmp_path, serve, anthropic=True)
    assert await probe.main(arguments()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['requests'] == 2 and result['cache_hit'] is True
    assert result['usage'] == [{'input_tokens': 150, 'output_tokens': 2,
                               'cache_read_tokens': 100, 'cache_write_tokens': 40}] * 2
    assert all(row['max_tokens'] == 64 and 'store' not in row
               and row['cache_control'] == {'type': 'ephemeral', 'ttl': '5m'} for row in attempts)
    assert all(client.is_closed for client, _ in catalog._owned_http_clients)
    assert 'PRIVATE' not in json.dumps(result)


@async_test
@pytest.mark.parametrize('flag,value', [('--deadline-seconds', 'nan'), ('--deadline-seconds', '0'),
                                        ('--output-tokens', '0'), ('--prefix-repetitions', '-1')])
async def test_probe_rejects_invalid_budgets_before_loading_config(monkeypatch, flag, value):
    def forbidden(*args):
        raise AssertionError('Invalid arguments must fail before config loading')
    monkeypatch.setattr('naiyue_test_agent.model_access.load_model_catalog', forbidden)
    with pytest.raises(SystemExit) as caught:
        await probe.main(['--model-id', 'main', flag, value])
    assert caught.value.code == 2
