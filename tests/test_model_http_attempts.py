"""Finite HTTP observations must preserve native SDK execution semantics."""

import asyncio
from dataclasses import asdict
from uuid import uuid4

import httpx
import pytest

from native_application_helpers import async_test
from http_observation_helpers import sdk_catalog, sdk_response
from naiyue_test_agent.agent_runtime import events as facts
from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services
from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback


def http_types():
    return facts.ModelHttpAttemptStartedData, facts.ModelHttpAttemptFinishedData


@pytest.mark.parametrize('status', ['response_headers', 'unconfirmed'])
def test_http_event_contract_and_registration(status):
    start, finish = http_types()
    data = finish('a', 'i', status, 503 if status == 'response_headers' else None,
                  0 if status == 'response_headers' else None)
    assert asdict(start('a', 'i')) == {'attempt_id': 'a', 'invocation_id': 'i'}
    assert data.status == status
    assert facts._PAYLOADS['model_http_attempt_started'] is start
    assert facts._PAYLOADS['model_http_attempt_finished'] is finish


@pytest.mark.parametrize('values', [('', 'i', 'unconfirmed', None, None),
    ('a', 'bad id', 'unconfirmed', None, None), ('a', 'i', 'failed', None, None),
    ('a', 'i', 'response_headers', True, 0), ('a', 'i', 'response_headers', 600, 0),
    ('a', 'i', 'unconfirmed', 200, None), ('a', 'i', 'unconfirmed', None, 0),
    ('a', 'i', 'response_headers', 200, -1)])
def test_http_event_rejects_invalid_facts(values):
    _, finish = http_types()
    with pytest.raises(ValueError):
        finish(*values)


@pytest.mark.parametrize('kind', ['openai', 'responses', 'anthropic', 'ollama', 'sync_fallback'])
@pytest.mark.parametrize('stream', [False, True])
@async_test
async def test_native_sdk_emits_one_paired_attempt_before_model_terminal(kind, stream):
    events = []
    async with sdk_catalog(kind) as catalog:
        install_model_usage_callback(catalog, http_attempt_limit=8)
        with bind_run_services(RunServices(publisher=facts.RunEventPublisher('s', 'r', (events.append,)))):
            model = catalog.get('A')
            result = [chunk async for chunk in model.astream('task')] if stream else await model.ainvoke('task')
            assert result
        assert [e.event_type for e in events] == ['model_call_started', 'model_http_attempt_started',
                                                'model_http_attempt_finished', 'model_call_finished']
        model_start, start, finish, model_end = events
        assert start.data.attempt_id == finish.data.attempt_id
        assert start.data.invocation_id == finish.data.invocation_id == model_start.data.invocation_id
        assert finish.data.status == 'response_headers' and finish.data.http_status == 200
        assert type(finish.data.response_headers_ms) is int and finish.data.response_headers_ms >= 0
        assert model_end.data.http_capture_status == 'complete'


@pytest.mark.parametrize('network_error', [False, True])
@async_test
async def test_sdk_retries_are_distinct_attempts_and_unconfirmed_is_not_guessed(network_error):
    count, events = [], []
    def serve(request):
        count.append(True)
        if len(count) < 3:
            if network_error:
                raise httpx.ConnectError('PRIVATE_NETWORK', request=request)
            return httpx.Response(503, headers={'retry-after-ms': '1'}, json={'error': {'message': 'PRIVATE'}})
        return sdk_response(request, 'openai')
    async with sdk_catalog(serve=serve) as catalog:
        install_model_usage_callback(catalog, http_attempt_limit=8)
        original_retries = catalog.get('A').root_async_client.max_retries
        with bind_run_services(RunServices(publisher=facts.RunEventPublisher('s', 'r', (events.append,)))):
            assert (await catalog.get('A').ainvoke('task')).content == 'done'
        assert catalog.get('A').root_async_client.max_retries == original_retries == 2
    starts = [e.data for e in events if e.event_type == 'model_http_attempt_started']
    ends = [e.data for e in events if e.event_type == 'model_http_attempt_finished']
    assert len(count) == len(starts) == len(ends) == 3
    assert len({e.attempt_id for e in starts}) == 3
    assert len({e.invocation_id for e in starts}) == 1
    assert sorted(e.status for e in ends) == (['response_headers', 'unconfirmed', 'unconfirmed']
                                              if network_error else ['response_headers'] * 3)
    assert all(e.http_status is None and e.response_headers_ms is None for e in ends if e.status == 'unconfirmed')


@async_test
async def test_attempt_limit_reports_gap_without_changing_success():
    from naiyue_test_agent.model_access.http_observation import HttpCallObservation, install_http_hooks
    emitted = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        token = install_http_hooks(client, asynchronous=True)
        call = HttpCallObservation('i', lambda *args: emitted.append(args), limit=1, tokens=(token,))
        call.bind()
        # A missing response keeps the first attempt pending at the observation layer.
        for _ in range(2):
            await client.event_hooks['request'][-1](client.build_request('GET', 'https://example.invalid'))
        assert call.close() == 'incomplete'
        assert [row[0] for row in emitted] == ['started', 'finished']
        assert emitted[-1][2:] == ('unconfirmed', None, None)
        assert call.closed and call.pending_count == 0


@async_test
async def test_duplicate_response_close_and_inherited_stale_context_emit_once():
    from naiyue_test_agent.model_access.http_observation import HttpCallObservation, install_http_hooks, current_http_call
    emitted = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        token = install_http_hooks(client, asynchronous=True)
        assert install_http_hooks(client, asynchronous=True) is token
        call = HttpCallObservation('i', lambda *args: emitted.append(args), limit=2, tokens=(token,))
        call.bind()
        response = await client.get('https://example.invalid')
        await client.event_hooks['response'][-1](response)
        assert await asyncio.create_task(asyncio.to_thread(call.close)) == 'complete'
        assert current_http_call() is None and call.pending_count == 0
        await client.event_hooks['response'][-1](response)
        assert call.close() == 'complete' and [row[0] for row in emitted] == ['started', 'finished']


@async_test
async def test_observer_failure_preserves_result_or_original_network_error(caplog):
    from naiyue_test_agent.model_access.http_observation import HttpCallObservation, install_http_hooks
    failure = httpx.ReadError('PRIVATE_NETWORK')
    def broken(*args):
        raise RuntimeError('PRIVATE_OBSERVER')
    def serve(request):
        if request.url.path == '/fail':
            raise failure
        return httpx.Response(200)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        token = install_http_hooks(client, asynchronous=True)
        call = HttpCallObservation('i', broken, limit=4, tokens=(token,))
        call.bind()
        assert (await client.get('https://example.invalid/ok')).status_code == 200
        with pytest.raises(httpx.ReadError) as caught:
            await client.get('https://example.invalid/fail')
        assert caught.value is failure and call.close() == 'incomplete'
    assert 'model_http_observation_failed' in caplog.text and 'PRIVATE' not in caplog.text


@async_test
async def test_cancelled_owner_closes_pending_attempt_once():
    from naiyue_test_agent.model_access.http_observation import current_http_call
    events, entered = [], asyncio.Event()
    async def serve(request):
        entered.set()
        await asyncio.Event().wait()
    async with sdk_catalog(serve=serve) as catalog:
        install_model_usage_callback(catalog, http_attempt_limit=4)
        async def run():
            with bind_run_services(RunServices(publisher=facts.RunEventPublisher('s', 'r', (events.append,)))):
                return await catalog.get('A').ainvoke('task')
        task = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        ends = [e for e in events if e.event_type == 'model_http_attempt_finished']
        assert len(ends) == 1 and ends[0].data.status == 'unconfirmed'
        assert events[-1].event_type == 'model_call_finished' and events[-1].data.status == 'cancelled'
        assert current_http_call() is None


@pytest.mark.parametrize('fault_at', ['prepare', 'close'])
@async_test
async def test_http_bookkeeping_fault_keeps_existing_model_facts(monkeypatch, caplog, fault_at):
    from naiyue_test_agent.agent_runtime import model_usage_observation as module
    from naiyue_test_agent.model_access.http_observation import HttpCallObservation
    def broken(*args, **kwargs):
        raise RuntimeError('PRIVATE_BOOKKEEPING')
    events = []
    async with sdk_catalog() as catalog:
        install_model_usage_callback(catalog, http_attempt_limit=4)
        monkeypatch.setattr(module, 'model_http_tokens', broken) if fault_at == 'prepare' else monkeypatch.setattr(
            HttpCallObservation, 'close', broken)
        with bind_run_services(RunServices(publisher=facts.RunEventPublisher('s', 'r', (events.append,)))):
            assert (await catalog.get('A').ainvoke('task')).content == 'done'
    model = [e for e in events if e.event_type in ('model_call_started', 'model_call_finished')]
    assert len(model) == 2 and model[-1].data.status == 'returned'
    assert model[-1].data.http_capture_status == 'incomplete'
    assert 'model_http_observation_failed' in caplog.text and 'PRIVATE' not in caplog.text


@async_test
async def test_catalog_close_drains_pending_observation_and_releases_ownership():
    from naiyue_test_agent.model_access.http_observation import HttpCallObservation, client_token
    emitted = []
    async with sdk_catalog() as catalog:
        client = catalog.get('A').root_async_client._client
        token = client_token(client)
        call = HttpCallObservation('i', lambda *args: emitted.append(args), limit=2, tokens=(token,))
        call.bind()
        await client.event_hooks['request'][-1](client.build_request('GET', 'https://example.invalid'))
        await catalog.aclose()
        assert client.is_closed and token.closed and not token.calls
        assert call.closed and call.pending_count == 0 and call._emit is None
        assert call.close() == 'incomplete'
        assert [row[0] for row in emitted] == ['started', 'finished']


@async_test
async def test_headers_200_survives_stream_failure_and_does_not_drain_body(caplog):
    from langchain_openai.chat_models.base import OpenAIConnectionError
    failure, events, consumed, closed = httpx.ReadError('PRIVATE_STREAM'), [], [], []
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True)
            row = {'id': 'stream', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'synthetic',
                'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'part'}, 'finish_reason': None}]}
            import json
            yield ('data: ' + json.dumps(row) + '\n\n').encode()
            raise failure
        async def aclose(self):
            closed.append(True)
    def serve(request):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Body())
    async with sdk_catalog(serve=serve) as catalog:
        # Establish the pinned SDK/adapter's existing wrapping contract first.
        with pytest.raises(OpenAIConnectionError) as baseline:
            _ = [chunk async for chunk in catalog.get('A').astream('task')]
        assert baseline.value.__cause__.__cause__ is failure
        install_model_usage_callback(catalog, http_attempt_limit=4)
        at_headers = []
        def receive(event):
            events.append(event)
            if event.event_type == 'model_http_attempt_finished':
                at_headers.append(len(consumed))
        with bind_run_services(RunServices(publisher=facts.RunEventPublisher('s', 'r', (receive,)))):
            with pytest.raises(OpenAIConnectionError) as caught:
                _ = [chunk async for chunk in catalog.get('A').astream('task')]
        assert caught.value.__cause__.__cause__ is failure
    response, = [e for e in events if e.event_type == 'model_http_attempt_finished']
    assert response.data.http_status == 200 and response.data.status == 'response_headers'
    assert events[-1].data.status == 'failed' and consumed and closed
    assert at_headers == [1] and len(consumed) == 2
    assert 'PRIVATE_STREAM' not in caplog.text


@async_test
async def test_existing_hooks_redirects_and_request_reuse_keep_fresh_attempts():
    from naiyue_test_agent.model_access.http_observation import HttpCallObservation, install_http_hooks
    emitted, order = [], []
    async def before_request(request):
        order.append('request')
    async def before_response(response):
        order.append('response')
    def serve(request):
        return httpx.Response(307, headers={'location': '/end'}) if request.url.path == '/begin' else httpx.Response(200)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve), follow_redirects=True,
            event_hooks={'request': [before_request], 'response': [before_response]}) as client:
        token = install_http_hooks(client, asynchronous=True)
        call = HttpCallObservation('i', lambda *args: emitted.append(args), limit=4, tokens=(token,))
        call.bind()
        request = client.build_request('GET', 'https://example.invalid/begin')
        assert (await client.send(request)).status_code == 200
        assert (await client.send(request)).status_code == 200
        assert call.close() == 'complete'
        assert client.event_hooks['request'][0] is before_request and client.event_hooks['response'][0] is before_response
    starts, ends = emitted[::2], emitted[1::2]
    assert len(starts) == 4 and len({row[1] for row in starts}) == 4
    assert [row[3] for row in ends] == [307, 200, 307, 200]
    assert order == ['request', 'response'] * 4


@async_test
async def test_ambiguous_overlapping_contexts_do_not_guess_an_identity():
    from naiyue_test_agent.model_access.http_observation import HttpCallObservation, install_http_hooks
    emitted = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        token = install_http_hooks(client, asynchronous=True)
        first = HttpCallObservation('first', lambda *args: emitted.append(args), limit=4, tokens=(token,))
        second = HttpCallObservation('second', lambda *args: emitted.append(args), limit=4, tokens=(token,))
        first.bind()
        second.bind()
        assert (await client.get('https://example.invalid')).status_code == 200
        assert first.close() == second.close() == 'incomplete'
        assert emitted == [] and not token.calls


@async_test
async def test_missing_model_callback_marks_run_capture_gap_instead_of_zero():
    from naiyue_test_agent.application.run_events import ApplicationEventSubscriptions, RunObservation
    subscriptions, events = ApplicationEventSubscriptions(), []
    subscriptions.subscribe(events.append)
    observation = RunObservation(subscriptions.publisher('s', 'r'), 'message')
    observation.start()
    async with sdk_catalog() as catalog:
        with observation.bind():
            assert (await catalog.get('A').ainvoke('task')).content == 'done'
    observation.result({}, None)
    observation.finish(False)
    assert events[-1].data.http_capture_status == 'incomplete'
    assert not any(e.event_type.startswith('model_http_attempt_') for e in events)


@async_test
async def test_http_clock_failure_preserves_headers_and_success(monkeypatch, caplog):
    from naiyue_test_agent.contracts import timing
    def broken():
        raise ValueError('PRIVATE_CLOCK')
    monkeypatch.setattr(timing, 'monotonic_ns', broken)
    events = []
    async with sdk_catalog() as catalog:
        install_model_usage_callback(catalog, http_attempt_limit=4)
        with bind_run_services(RunServices(publisher=facts.RunEventPublisher('s', 'r', (events.append,)))):
            assert (await catalog.get('A').ainvoke('task')).content == 'done'
    end, = [e for e in events if e.event_type == 'model_http_attempt_finished']
    assert end.data.status == 'response_headers' and end.data.http_status == 200
    assert end.data.response_headers_ms is None and events[-1].data.status == 'returned'
    assert 'duration_observation_failed' in caplog.text and 'PRIVATE_CLOCK' not in caplog.text


@async_test
async def test_hook_installation_failure_does_not_prevent_catalog_or_model(monkeypatch, caplog):
    from naiyue_test_agent.model_access import http_observation
    def broken():
        raise RuntimeError('PRIVATE_INSTALL')
    monkeypatch.setattr(http_observation, '_ClientToken', broken)
    events = []
    async with sdk_catalog() as catalog:
        install_model_usage_callback(catalog, http_attempt_limit=4)
        with bind_run_services(RunServices(publisher=facts.RunEventPublisher('s', 'r', (events.append,)))):
            assert (await catalog.get('A').ainvoke('task')).content == 'done'
        assert events[-1].data.http_capture_status == 'unavailable'
    assert 'model_http_observation_failed' in caplog.text and 'PRIVATE_INSTALL' not in caplog.text


@pytest.mark.parametrize('blocked', ['limit', 'missing', 'disabled'])
@async_test
async def test_copied_request_marker_never_finishes_another_context(blocked):
    from contextvars import Context
    from naiyue_test_agent.model_access.http_observation import HttpCallObservation, install_http_hooks
    first_events, other_events = [], []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(201))) as client:
        token = install_http_hooks(client, asynchronous=True)
        first = HttpCallObservation('first', lambda *args: first_events.append(args), limit=2, tokens=(token,))
        first.bind()
        original = client.build_request('GET', 'https://example.invalid')
        await client.event_hooks['request'][-1](original)
        async def independent_call():
            other = None
            if blocked != 'missing':
                other = HttpCallObservation('other', lambda *args: other_events.append(args), limit=1,
                                            tokens=() if blocked == 'disabled' else (token,))
                other.bind()
                if blocked == 'limit':
                    await client.event_hooks['request'][-1](client.build_request('GET', 'https://example.invalid'))
            copied = client.build_request('GET', 'https://example.invalid', extensions=dict(original.extensions))
            assert (await client.send(copied)).status_code == 201
            if other is not None:
                other.close()
        await asyncio.create_task(independent_call(), context=Context())
        first.close()
        assert [row[0] for row in first_events] == ['started', 'finished']
        assert first_events[-1][2:] == ('unconfirmed', None, None)
        assert all(row[2] == 'unconfirmed' for row in other_events if row[0] == 'finished')
