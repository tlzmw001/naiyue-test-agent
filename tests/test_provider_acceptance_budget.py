"""Acceptance limits apply before transport, including SDK-internal retry paths."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

import httpx
import httpx2
import openai
import pytest
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import OpenAIAPIError

from naiyue_test_agent.model_access import ModelCatalog
from scripts.provider_acceptance_budget import AcceptanceBudgetStop, RequestBudget, guard_catalog


def test_case_batch_and_total_share_counts_and_stop_the_whole_acceptance():
    total = RequestBudget(60)
    batch = total.child(8)
    case = batch.child(2)
    case.reserve('main')
    case.reserve('review-fast')
    assert total.snapshot()['used'] == batch.snapshot()['used'] == case.snapshot()['used'] == 2
    assert total.snapshot()['models'] == {'main': 1, 'review-fast': 1}
    with pytest.raises(AcceptanceBudgetStop):
        case.reserve('summary')
    assert total.snapshot()['used'] == 2 and total.snapshot()['stopped'] is True
    with pytest.raises(AcceptanceBudgetStop):
        batch.child(2).reserve('child')
    assert total.snapshot()['used'] == 2


def test_parallel_requests_cannot_share_the_last_quota():
    budget = RequestBudget(8)

    def claim(_):
        try:
            budget.reserve('child')
            return True
        except AcceptanceBudgetStop:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        accepted = list(pool.map(claim, range(64)))
    assert sum(accepted) == budget.snapshot()['used'] == 8
    assert budget.snapshot()['models'] == {'child': 8}


@pytest.mark.parametrize('limit', [0, -1, True, 1.5])
def test_budget_rejects_invalid_limits(limit):
    with pytest.raises(ValueError):
        RequestBudget(limit)


def test_labels_and_child_limits_are_validated_without_consuming_quota():
    budget = RequestBudget(2)
    with pytest.raises(ValueError):
        budget.child(3)
    for label in ('private\nlabel', '', 'x' * 129):
        with pytest.raises(ValueError):
            budget.reserve(label)
    assert budget.snapshot()['used'] == 0


def test_sdk_async_retry_is_disabled_and_budget_refuses_before_transport(monkeypatch):
    async def scenario():
        attempts = []

        async def reject(transport, request):
            attempts.append(request.method)
            response = httpx.Response if isinstance(request, httpx.Request) else httpx2.Response
            return response(503, json={'error': {'message': 'synthetic failure'}})

        for module in (httpx, httpx2):
            monkeypatch.setattr(module.AsyncHTTPTransport, 'handle_async_request', reject)
        model = ChatOpenAI(model='synthetic', api_key='synthetic', base_url='https://example.invalid/v1',
                           max_retries=2, use_responses_api=False)
        catalog, budget = ModelCatalog('main', {'main': model}), RequestBudget(1)
        try:
            guard_catalog(catalog, budget)
            assert model.max_retries == model.root_client.max_retries == model.root_async_client.max_retries == 0
            with pytest.raises(OpenAIAPIError) as rejected:
                await model.ainvoke('synthetic', config={'callbacks': []})
            assert isinstance(rejected.value.__cause__, openai.InternalServerError)
            with pytest.raises(AcceptanceBudgetStop):
                await model.model_copy().ainvoke('synthetic', config={'callbacks': []})
            assert attempts == ['POST'] and budget.snapshot()['used'] == 1
        finally:
            await catalog.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('responses', [False, True])
def test_sync_and_async_sdk_clients_use_the_same_budget(monkeypatch, responses):
    async def scenario():
        attempts = []

        def reply(transport, request):
            attempts.append(request.method)
            response = httpx.Response if isinstance(request, httpx.Request) else httpx2.Response
            if responses:
                payload = {'id': 'resp_synthetic', 'object': 'response', 'created_at': 1,
                    'status': 'completed', 'model': 'synthetic', 'error': None, 'incomplete_details': None,
                    'output': [{'id': 'msg_synthetic', 'type': 'message', 'status': 'completed',
                        'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'ok', 'annotations': []}]}]}
            else:
                payload = {'id': 'synthetic', 'model': 'synthetic', 'choices': [
                    {'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]}
            return response(200, json=payload)

        async def async_reply(transport, request):
            return reply(transport, request)

        for module in (httpx, httpx2):
            monkeypatch.setattr(module.HTTPTransport, 'handle_request', reply)
            monkeypatch.setattr(module.AsyncHTTPTransport, 'handle_async_request', async_reply)
        model = ChatOpenAI(model='synthetic', api_key='synthetic', base_url='https://example.invalid/v1',
                           use_responses_api=responses)
        catalog, budget = ModelCatalog('main', {'main': model}), RequestBudget(2)
        try:
            guard_catalog(catalog, budget)
            assert (await asyncio.to_thread(model.invoke, 'synthetic')).text == 'ok'
            assert (await model.ainvoke('synthetic')).text == 'ok'
            with pytest.raises(AcceptanceBudgetStop):
                await model.ainvoke('synthetic')
            assert attempts == ['POST', 'POST']
            assert budget.snapshot()['models'] == {'main': 2}
            with pytest.raises(ValueError, match='already'):
                guard_catalog(catalog, budget)
        finally:
            await catalog.aclose()
    asyncio.run(scenario())


def test_auto_empty_callbacks_cannot_convert_budget_stop_into_ask(monkeypatch):
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer
    from naiyue_test_agent.tools.permission_rules import AutoPermissionSettings

    async def scenario():
        attempts = []

        async def reply(transport, request):
            attempts.append(request.method)
            response = httpx.Response if isinstance(request, httpx.Request) else httpx2.Response
            return response(200, json={'id': 'synthetic', 'model': 'synthetic', 'choices': [
                {'index': 0, 'message': {'role': 'assistant', 'content': '{"decision":"ask","reason":"uncertain"}'},
                 'finish_reason': 'stop'}]})

        for module in (httpx, httpx2):
            monkeypatch.setattr(module.AsyncHTTPTransport, 'handle_async_request', reply)
        model = ChatOpenAI(model='synthetic', api_key='synthetic', base_url='https://example.invalid/v1',
                           use_responses_api=False, metadata={'naiyue_model_limits': {
                               'context_window_tokens': 128000, 'max_output_tokens': 1024}})
        catalog, budget = ModelCatalog('main', {'main': model}), RequestBudget(1)
        reviewer = AutoPermissionReviewer(AutoPermissionSettings(fast_model_id='main', detailed_model_id='main'),
                                           model, model)
        try:
            guard_catalog(catalog, budget)
            data = {'human_messages': [{'sequence': 0, 'content': 'Inspect the synthetic file.'}],
                    'project_instructions': '', 'previous_calls': [], 'history_truncated': False,
                    'current_call': {'name': 'read', 'args': {'path': 'test.txt'}}, 'subject': {}}
            with pytest.raises(AcceptanceBudgetStop):
                await reviewer.areview(data, {})
            assert attempts == ['POST'] and budget.snapshot()['used'] == 1
            assert budget.snapshot()['stopped'] is True
        finally:
            await catalog.aclose()
    asyncio.run(scenario())


def test_stream_is_one_http_attempt_and_cannot_restart_after_the_limit(monkeypatch):
    async def scenario():
        attempts = []

        async def stream(transport, request):
            attempts.append(request.method)
            chunks = [{'id': 'stream_synthetic', 'object': 'chat.completion.chunk', 'model': 'synthetic',
                'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': None}]},
                {'id': 'stream_synthetic', 'object': 'chat.completion.chunk', 'model': 'synthetic',
                 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}]
            body = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
            response = httpx.Response if isinstance(request, httpx.Request) else httpx2.Response
            return response(200, headers={'content-type': 'text/event-stream'}, content=body.encode())

        for module in (httpx, httpx2):
            monkeypatch.setattr(module.AsyncHTTPTransport, 'handle_async_request', stream)
        model = ChatOpenAI(model='synthetic', api_key='synthetic', base_url='https://example.invalid/v1',
                           use_responses_api=False)
        catalog, budget = ModelCatalog('main', {'main': model}), RequestBudget(1)
        try:
            guard_catalog(catalog, budget)
            parts = [part async for part in model.astream('synthetic')]
            assert ''.join(part.text for part in parts) == 'ok'
            with pytest.raises(AcceptanceBudgetStop):
                async for part in model.astream('synthetic'):
                    raise AssertionError('An exhausted budget yielded another response')
            assert attempts == ['POST'] and budget.snapshot()['used'] == 1
        finally:
            await catalog.aclose()
    asyncio.run(scenario())


def test_unsupported_catalog_fails_before_preparing_http_clients():
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    catalog = ModelCatalog('fake', {'fake': FakeMessagesListChatModel(responses=[])})
    with pytest.raises(ValueError, match='only OpenAI-compatible'):
        guard_catalog(catalog, RequestBudget(1))
    assert catalog._owned_http_clients == []
