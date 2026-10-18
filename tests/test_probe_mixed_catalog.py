"""Probe limits preserve mixed-provider catalogs and every adapter's HTTP boundary."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama

from naiyue_test_agent.model_access import ModelCatalog
from scripts.provider_acceptance_budget import AcceptanceBudgetStop, RequestBudget, guard_catalog
from test_bounded_model_probes import install_transport


def test_openai_probe_can_select_a_model_from_a_mixed_catalog(monkeypatch, capsys):
    from scripts.probe_async_model import _worker
    catalog, attempts, guarded = install_transport(monkeypatch)
    catalog._models.update(anthropic=ChatAnthropic(model='synthetic', api_key='synthetic'),
                           ollama=ChatOllama(model='synthetic'))
    asyncio.run(_worker(SimpleNamespace(config='unused', model_id='main',
                                      timeout_seconds=5, with_context=False)))
    assert 'PASS' in capsys.readouterr().out and len(attempts) == 1
    assert guarded[0].snapshot()['models'] == {'main': 1}
    assert all(client.is_closed for client, _ in catalog._owned_http_clients)


@pytest.mark.parametrize('provider', ['anthropic', 'ollama'])
@pytest.mark.parametrize('synchronous', [False, True])
def test_each_existing_adapter_counts_before_transport_and_rejects_second_attempt(monkeypatch, provider, synchronous):
    async def scenario():
        attempts = []
        def reply(transport, request):
            attempts.append(request.method)
            response = httpx.Response if isinstance(request, httpx.Request) else httpx2.Response
            if provider == 'anthropic':
                return response(200, json={'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant',
                    'model': 'synthetic', 'content': [{'type': 'text', 'text': 'ok'}],
                    'stop_reason': 'end_turn', 'usage': {'input_tokens': 1, 'output_tokens': 1}})
            data = {'model': 'synthetic', 'message': {'role': 'assistant', 'content': 'ok'},
                    'done': True, 'done_reason': 'stop', 'prompt_eval_count': 1, 'eval_count': 1}
            return response(200, headers={'content-type': 'application/x-ndjson'},
                            content=(json.dumps(data) + '\n').encode())
        async def async_reply(transport, request):
            return reply(transport, request)
        for module in (httpx, httpx2):
            monkeypatch.setattr(module.HTTPTransport, 'handle_request', reply)
            monkeypatch.setattr(module.AsyncHTTPTransport, 'handle_async_request', async_reply)
        model = (ChatAnthropic(model='synthetic', api_key='synthetic', max_retries=2)
                 if provider == 'anthropic' else ChatOllama(model='synthetic'))
        catalog, budget = ModelCatalog('main', {'main': model}), RequestBudget(1)
        async def invoke():
            if synchronous:
                return await asyncio.to_thread(model.invoke, 'synthetic')
            return await model.ainvoke('synthetic')
        try:
            guard_catalog(catalog, budget)
            assert (await invoke()).text == 'ok'
            with pytest.raises(AcceptanceBudgetStop):
                await invoke()
            assert attempts == ['POST'] and budget.snapshot()['models'] == {'main': 1}
            if provider == 'anthropic':
                assert model.max_retries == model._client.max_retries == model._async_client.max_retries == 0
        finally:
            await catalog.aclose()
        assert all(client.is_closed for client, _ in catalog._owned_http_clients)
    asyncio.run(scenario())


def test_anthropic_probe_does_not_retry_failed_http_request(monkeypatch):
    async def scenario():
        attempts = []
        async def reject(transport, request):
            attempts.append(True)
            response = httpx.Response if isinstance(request, httpx.Request) else httpx2.Response
            return response(503, json={'type': 'error', 'error': {'type': 'api_error', 'message': 'synthetic'}})
        for module in (httpx, httpx2):
            monkeypatch.setattr(module.AsyncHTTPTransport, 'handle_async_request', reject)
        model = ChatAnthropic(model='synthetic', api_key='synthetic', max_retries=2)
        catalog, budget = ModelCatalog('main', {'main': model}), RequestBudget(1)
        try:
            guard_catalog(catalog, budget)
            with pytest.raises(Exception) as caught:
                await model.ainvoke('synthetic')
            assert not isinstance(caught.value, AcceptanceBudgetStop)
            with pytest.raises(AcceptanceBudgetStop):
                await model.ainvoke('synthetic')
            assert attempts == [True] and budget.snapshot()['used'] == 1
        finally:
            await catalog.aclose()
    asyncio.run(scenario())
