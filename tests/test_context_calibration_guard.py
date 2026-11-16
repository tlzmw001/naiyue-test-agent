"""All probe adapters stop at the owned HTTP boundary before a second send."""
import asyncio

import httpx
import httpx2
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from naiyue_test_agent.model_access import ModelCatalog
from scripts.provider_acceptance_budget import AcceptanceBudgetStop, RequestBudget, guard_catalog


@pytest.mark.parametrize(('protocol', 'path'), [
    ('completions', '/v1/chat/completions'),
    ('responses', '/v1/responses'),
    ('anthropic', '/v1/messages'),
    ('ollama', '/api/chat'),
])
def test_each_protocol_reaches_transport_once_then_refuses_before_send(monkeypatch, protocol, path):
    async def scenario():
        attempts = []

        async def unavailable(transport, request):
            attempts.append((request.method, request.url.path))
            response = httpx.Response if isinstance(request, httpx.Request) else httpx2.Response
            return response(503, json={'error': {'type': 'overloaded_error', 'message': 'synthetic unavailable'}})

        for module in (httpx, httpx2):
            monkeypatch.setattr(module.AsyncHTTPTransport, 'handle_async_request', unavailable)
        if protocol == 'anthropic':
            model = ChatAnthropic(model='synthetic', api_key='synthetic',
                                  base_url='https://example.invalid', max_retries=2)
        elif protocol == 'ollama':
            model = ChatOllama(model='synthetic', base_url='https://example.invalid')
        else:
            model = ChatOpenAI(model='synthetic', api_key='synthetic',
                base_url='https://example.invalid/v1', max_retries=2,
                use_responses_api=protocol == 'responses')
        catalog, budget = ModelCatalog('main', {'main': model}), RequestBudget(1)
        try:
            guard_catalog(catalog, budget)
            if protocol in ('responses', 'completions'):
                assert model.max_retries == model.root_client.max_retries == model.root_async_client.max_retries == 0
            elif protocol == 'anthropic':
                assert model.max_retries == model._client.max_retries == model._async_client.max_retries == 0
            with pytest.raises(Exception) as failed:
                await model.ainvoke('synthetic', config={'callbacks': []})
            assert not isinstance(failed.value, AcceptanceBudgetStop)
            provider_error = failed.value.__cause__ if protocol in ('completions', 'responses') else failed.value
            assert provider_error.status_code == 503
            assert attempts == [('POST', path)]
            with pytest.raises(AcceptanceBudgetStop):
                await model.model_copy().ainvoke('synthetic', config={'callbacks': []})
            assert attempts == [('POST', path)]
            assert budget.snapshot() == {'limit': 1, 'used': 1, 'stopped': True, 'models': {'main': 1}}
        finally:
            await catalog.aclose()
    asyncio.run(scenario())
