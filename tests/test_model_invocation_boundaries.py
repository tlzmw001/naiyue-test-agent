"""Invocation observation closes once, preserves scopes, and tolerates faults."""

import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from native_application_helpers import async_test
from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services
from test_model_invocation_observation import callback, metadata, output


@async_test
@pytest.mark.parametrize('cancel', [True, False])
async def test_owner_task_closes_missing_framework_error_hook_once(monkeypatch, cancel):
    from naiyue_test_agent.contracts import timing
    tick = [0]
    monkeypatch.setattr(timing, 'monotonic_ns', lambda: tick[0])
    events, identity = [], uuid4()
    handler, entered, release, closed = callback(), asyncio.Event(), asyncio.Event(), asyncio.Event()
    failure = RuntimeError('PRIVATE_FAILURE')
    async def call():
        with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', (events.append,)))):
            await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata())
            entered.set()
            await release.wait()
            raise failure
    task = asyncio.create_task(call())
    task.add_done_callback(lambda _: closed.set())
    await entered.wait()
    tick[0] = 17_900_000
    task.cancel() if cancel else release.set()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError) as caught:
        await task
    if not cancel:
        assert caught.value is failure
    await closed.wait()
    await handler.on_llm_error(failure, run_id=identity)  # A late hook cannot duplicate completion.
    await handler.on_llm_end(output(None), run_id=identity)
    assert len(events) == 2 and handler._active == {}
    assert events[-1].data.status == ('cancelled' if cancel else 'failed')
    assert events[-1].data.usage_status == 'unavailable'
    assert events[-1].data.duration_ms == 17
    assert 'PRIVATE' not in repr(events)


@async_test
async def test_completed_invocation_uses_start_scope_after_binding_changes():
    a, b, identity, handler = [], [], uuid4(), callback()
    with bind_run_services(RunServices(publisher=RunEventPublisher('a', 'r-a', (a.append,)))):
        await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata())
    with bind_run_services(RunServices(publisher=RunEventPublisher('b', 'r-b', (b.append,)))):
        await handler.on_llm_end(output(None), run_id=identity)
    assert b == [] and len(a) == 2 and {e.session_id for e in a} == {'a'}
    assert a[-1].data.usage_status == 'unavailable' and handler._active == {}


@async_test
async def test_duplicate_start_and_finish_are_observable_and_do_not_duplicate_facts(caplog):
    events, identity, handler = [], uuid4(), callback()
    with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', (events.append,)))):
        for _ in range(2):
            await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata())
        for _ in range(2):
            await handler.on_llm_end(output(None), run_id=identity)
    assert len(events) == 2 and handler._active == {}
    assert 'model_usage_duplicate_start' in caplog.text


@async_test
@pytest.mark.parametrize('tags', [['naiyue_permission_review'], ['naiyue_summary', 'naiyue_permission_review_fast']])
async def test_ambiguous_purpose_is_unknown_with_a_fixed_diagnostic(tags, caplog):
    events, identity, handler = [], uuid4(), callback()
    with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', (events.append,)))):
        await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata(), tags=tags)
        await handler.on_llm_end(output(None), run_id=identity)
    assert events[0].data.purpose is None and 'model_usage_purpose_unavailable' in caplog.text


@async_test
async def test_sync_model_async_fallback_releases_completed_records():
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    model = FakeMessagesListChatModel(responses=[AIMessage(content='done')], callbacks=[callback()], metadata=metadata())
    events = []
    with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', (events.append,)))):
        assert (await model.ainvoke('PRIVATE_INPUT')).content == 'done'
    assert len(events) == 2 and events[-1].data.status == 'returned'
    assert model.callbacks[0]._active == {}


@async_test
@pytest.mark.parametrize('response', [None, LLMResult(generations=[]), LLMResult(generations=[[], []])])
async def test_absent_or_ambiguous_result_does_not_invent_usage(response):
    events, identity, handler = [], uuid4(), callback()
    with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', (events.append,)))):
        await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata())
        await handler.on_llm_end(response, run_id=identity)
    assert events[-1].data.status == 'returned' and events[-1].data.usage_status == 'unavailable'
    assert events[-1].data.input_tokens is None and handler._active == {}


def test_event_checker_rejects_unpaired_invocations_before_isolated_delivery():
    from event_sequence import CheckedPublisher
    from naiyue_test_agent.agent_runtime.events import ModelInvocationStartedData, ModelInvocationFinishedData, RunStartedData
    publisher = CheckedPublisher('s', 'r', ())
    publisher.publish('run_started', RunStartedData('message'))
    with pytest.raises(ValueError, match='has no start'):
        publisher.publish('model_call_finished', ModelInvocationFinishedData('missing', 'returned'))
    publisher.publish('model_call_started', ModelInvocationStartedData('call', 'A', 'agent'))
    with pytest.raises(ValueError, match='already started'):
        publisher.publish('model_call_started', ModelInvocationStartedData('call', 'A', 'agent'))
    publisher.publish('model_call_finished', ModelInvocationFinishedData('call', 'returned'))


def test_web_serialization_keeps_invocation_identity_and_unknown_usage_explicit():
    from naiyue_test_agent.agent_runtime.events import ModelInvocationStartedData, ModelInvocationFinishedData
    from naiyue_test_agent.web.serialization import wire_event, project_event_for_web
    events = []
    publisher = RunEventPublisher('s', 'r', (events.append,))
    publisher.publish('model_call_started', ModelInvocationStartedData('call', 'A', 'auto_fast'))
    publisher.publish('model_call_finished', ModelInvocationFinishedData('call', 'cancelled'))
    rows = [wire_event(project_event_for_web(event)) for event in events]
    assert rows[0]['data'] == {'invocation_id': 'call', 'model_id': 'A', 'purpose': 'auto_fast'}
    assert rows[1]['data']['invocation_id'] == 'call' and rows[1]['data']['usage_status'] == 'unavailable'
    assert all(rows[1]['data'][key] is None for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'))


@async_test
@pytest.mark.parametrize('error_metadata', [False, True])
async def test_native_stream_error_keeps_partial_usage_and_original_exception(error_metadata):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk
    failure = RuntimeError('PRIVATE_STREAM_FAILURE')
    if error_metadata:
        from types import SimpleNamespace
        failure.response = SimpleNamespace(json=lambda: {'PRIVATE': 'BODY'}, headers={'PRIVATE': 'HEADER'}, status_code=500)
    class PartialModel(FakeListChatModel):
        async def _astream(self, *args, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content='PRIVATE_CHUNK', usage_metadata={
                'input_tokens': 100, 'output_tokens': 1, 'total_tokens': 101,
                'input_token_details': {'cache_read': 20}}))
            raise failure
    handler, events = callback(), []
    model = PartialModel(responses=[], callbacks=[handler], metadata=metadata())
    with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', (events.append,)))):
        with pytest.raises(RuntimeError) as caught:
            async for _ in model.astream('PRIVATE_TASK'):
                pass
    assert caught.value is failure and handler._active == {}
    assert len(events) == 2 and events[-1].data.status == 'failed'
    assert events[-1].data.usage_status == 'partial'
    assert (events[-1].data.input_tokens, events[-1].data.output_tokens, events[-1].data.cache_read_tokens) == (100, 1, 20)
    assert 'PRIVATE' not in repr(events)


@async_test
async def test_native_stream_cancellation_keeps_partial_usage_without_duplicate_finish():
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk
    entered, release, events, handler = asyncio.Event(), asyncio.Event(), [], callback()
    class StreamingModel(FakeListChatModel):
        async def _astream(self, *args, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content='partial', usage_metadata={
                'input_tokens': 100, 'output_tokens': 1, 'total_tokens': 101}))
            entered.set()
            await release.wait()
    model = StreamingModel(responses=[], callbacks=[handler], metadata=metadata())
    async def call():
        with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', (events.append,)))):
            async for _ in model.astream('task'):
                pass
    task = asyncio.create_task(call())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert len(events) == 2 and handler._active == {}
    assert events[-1].data.status == 'cancelled' and events[-1].data.usage_status == 'partial'
    assert (events[-1].data.input_tokens, events[-1].data.output_tokens) == (100, 1)
