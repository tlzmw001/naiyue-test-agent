"""Native async model IO keeps the existing selection/gate/event contract."""

from native_application_helpers import async_test

from naiyue_test_agent.application import prepare_tool_registry

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents.middleware import ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp
from pydantic import Field

from naiyue_test_agent.agent_runtime import build_runtime_agent, make_model_selection_middleware
from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected


class NativeAsyncModel(BaseChatModel):
    response: AIMessage
    entered: Any = None
    release: Any = None
    error: Any = None
    inputs: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return 'native-async-test'

    def _generate(self, *args, **kwargs):
        raise AssertionError('Synchronous model IO must not be used')

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.inputs.append(messages)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return ChatResult(generations=[ChatGeneration(message=self.response.model_copy(deep=True))])


def runtime(models, selected, selections=None):
    _runtime_registry_1 = prepare_tool_registry([])
    _runtime_names_1 = _runtime_registry_1.names()
    def resolve_model(sid, agent_path='root'):
        model_id = selected[sid]
        if selections is not None:
            selections.append((sid, model_id))
        return model_id, models[model_id]

    return build_runtime_agent(
        next(iter(models.values())), tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
        resolve_model=resolve_model,
        checkpointer=InMemorySaver(),
    )


def context(events=None, sid='s', rid='r'):
    result = {'session_id': sid, 'run_id': rid}
    if events is not None:
        publisher = RunEventPublisher(sid, rid, (events.append,))
        publisher.publish('run_started', RunStartedData('message'))
        result['_run_event_publisher'] = publisher
    return result


def config(sid='s'):
    return {'configurable': {'thread_id': sid}}


def test_native_model_wait_does_not_block_other_coroutine():
    async def scenario():
        entered, release, other_ran = asyncio.Event(), asyncio.Event(), asyncio.Event()
        model = NativeAsyncModel(response=AIMessage(content='answer', response_metadata={'finish_reason': 'stop'}),
                                 entered=entered, release=release)
        graph = runtime({'A': model}, {'s': 'A'})
        task = asyncio.create_task(graph.ainvoke({'messages': [('user', 'question')]},
                                                context=context(), config=config()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert not task.done()

            async def receive_control():
                other_ran.set()

            await receive_control()
            assert other_ran.is_set() and not task.done()
            release.set()
            result = await asyncio.wait_for(task, 5)
            assert result['messages'][-1].content == 'answer'
            assert result['messages'][-1].response_metadata['naiyue_stop_reason'] == 'stop'
            snapshot = await graph.aget_state(config())
            assert [m.type for m in snapshot.values['messages']] == ['human', 'ai']
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_native_async_selection_switch_and_checkpoint_isolation():
    async def scenario():
        models = {mid: NativeAsyncModel(response=AIMessage(content=mid)) for mid in ('A', 'B')}
        selected, selections = {'s': 'A', 'other': 'B'}, []
        graph = runtime(models, selected, selections)
        await graph.ainvoke({'messages': [('user', 'first')]}, context=context(), config=config())
        selected['s'] = 'B'
        result = await graph.ainvoke({'messages': [('user', 'second')]}, context=context(rid='r2'), config=config())
        other = await graph.ainvoke({'messages': [('user', 'isolated')]},
                                   context=context(sid='other'), config=config('other'))
        assert selections == [('s', 'A'), ('s', 'B'), ('other', 'B')]
        assert [m.content for m in result['messages']] == ['first', 'A', 'second', 'B']
        assert [m.content for m in other['messages']] == ['isolated', 'B']
        assert [(m.type, m.content) for m in models['B'].inputs[0]] == [
            ('system', ''), ('human', 'first'), ('ai', 'A'), ('human', 'second'),
        ]
    asyncio.run(scenario())


@pytest.mark.parametrize(('message', 'reason'), [
    (AIMessage(content='PRIVATE', response_metadata={'finish_reason': 'content_filter'}), 'stop_reason_error'),
    (AIMessage(content='', invalid_tool_calls=[{'id': 'bad', 'name': 'read', 'args': '{bad', 'error': 'PRIVATE'}]),
     'invalid_tool_calls'),
    (AIMessage(content='', tool_calls=[{'id': 'valid', 'name': 'read', 'args': {}}],
               invalid_tool_calls=[{'id': 'bad', 'name': 'read', 'args': '{bad', 'error': 'PRIVATE'}]),
     'invalid_tool_calls'),
    (AIMessage(content='', response_metadata={'finish_reason': 'tool_calls'}), 'missing_tool_calls'),
])
def test_async_gate_rejects_unusable_response(message, reason, caplog):
    async def scenario():
        events = []
        graph = runtime({'A': NativeAsyncModel(response=message)}, {'s': 'A'})
        with pytest.raises(ModelResponseRejected) as caught:
            await graph.ainvoke({'messages': [('user', 'PRIVATE')]}, context=context(events), config=config())
        assert caught.value.reason == reason
        assert [e.event_type for e in events] == ['run_started', 'model_call_started', 'model_context_snapshot', 'model_context_snapshot', 'model_call_finished']
        assert events[-1].data.status == 'failed'
        assert events[-1].data.failure.reason == reason
        assert not any(isinstance(m, AIMessage) for m in (await graph.aget_state(config())).values['messages'])
        assert 'PRIVATE' not in caplog.text + str(caught.value)
    asyncio.run(scenario())


def test_async_original_error_and_safe_failure_event(caplog):
    async def scenario():
        events = []
        error = RuntimeError('PRIVATE PROVIDER BODY')
        graph = runtime({'A': NativeAsyncModel(response=AIMessage(content='unused'), error=error)}, {'s': 'A'})
        with pytest.raises(RuntimeError) as caught:
            await graph.ainvoke({'messages': [('user', 'PRIVATE')]}, context=context(events), config=config())
        assert caught.value is error
        assert events[-1].data.failure.error_type == 'RuntimeError'
        assert events[-1].data.failure.category == 'unknown'
        assert 'PRIVATE' not in caplog.text + str(events)
    asyncio.run(scenario())


def test_async_usage_unknown_reason_and_call_pairing(caplog):
    async def scenario():
        events = []
        model = NativeAsyncModel(response=AIMessage(content='PRIVATE',
            response_metadata={'finish_reason': 'future_provider_reason'},
            usage_metadata={'input_tokens': 9, 'output_tokens': 2, 'total_tokens': 11,
                            'input_token_details': {'cache_read': 4}}))
        graph = runtime({'A': model}, {'s': 'A'})
        with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
            result = await graph.ainvoke({'messages': [('user', 'PRIVATE')]}, context=context(events), config=config())
        assert result['messages'][-1].response_metadata['naiyue_stop_reason'] == 'unknown'
        start, prepared, returned, end = events[1:]
        assert prepared.data.model_call_id == returned.data.model_call_id == start.data.model_call_id
        assert start.data.model_call_id == end.data.model_call_id
        assert (end.data.input_tokens, end.data.output_tokens, end.data.cache_read_tokens) == (9, 2, 4)
        assert any(hasattr(record, 'model_response') for record in caplog.records)
        assert 'PRIVATE' not in caplog.text + str(events)
    asyncio.run(scenario())


def test_task_cancellation_propagates_and_closes_model_call():
    async def scenario():
        entered, release, events = asyncio.Event(), asyncio.Event(), []
        graph = runtime({'A': NativeAsyncModel(response=AIMessage(content='unused'), entered=entered, release=release)},
                        {'s': 'A'})
        task = asyncio.create_task(graph.ainvoke({'messages': [('user', 'question')]},
                                                context=context(events), config=config()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert events[-1].event_type == 'model_call_finished'
            assert events[-1].data.status == 'failed'
            assert events[-1].data.failure.category == 'cancelled'
            assert events[-1].data.failure.reason == 'task_cancelled'
            assert not any(isinstance(m, AIMessage) for m in (await graph.aget_state(config())).values['messages'])
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('messages', [[], [HumanMessage(content='PRIVATE')]])
def test_async_missing_ai_message_is_rejected(messages):
    async def scenario():
        middleware = make_model_selection_middleware(lambda _, agent_path='root': ('A', object()))
        request = SimpleNamespace(runtime=SimpleNamespace(context=context()))
        request.override = lambda **_: request

        async def handler(_request):
            return ModelResponse(result=messages)

        with pytest.raises(ModelResponseRejected) as caught:
            await middleware.awrap_model_call(request, handler)
        assert caught.value.reason == 'missing_ai_message'
    asyncio.run(scenario())


def test_async_graph_control_exception_is_preserved():
    async def scenario():
        events, error = [], GraphBubbleUp('PRIVATE')
        middleware = make_model_selection_middleware(lambda _, agent_path='root': ('A', object()))
        request = SimpleNamespace(runtime=SimpleNamespace(context=context(events)))
        request.override = lambda **_: request

        async def handler(_request):
            raise error

        with pytest.raises(GraphBubbleUp) as caught:
            await middleware.awrap_model_call(request, handler)
        assert caught.value is error
        assert [e.event_type for e in events] == ['run_started', 'model_call_started']
    asyncio.run(scenario())


@async_test
async def test_request_override_failure_preserves_exception_and_closes_call():
    events, error = [], ValueError('PRIVATE override failure')
    middleware = make_model_selection_middleware(lambda _, agent_path='root': ('A', object()))
    request = SimpleNamespace(runtime=SimpleNamespace(context=context(events)))

    def override(**_kwargs):
        raise error

    request.override = override

    async def async_handler(_request):
        raise AssertionError('No model call should be made')

    with pytest.raises(ValueError) as caught:
        await middleware.awrap_model_call(request, async_handler)
    assert caught.value is error
    assert [e.event_type for e in events] == ['run_started', 'model_call_started', 'model_call_finished']
    assert events[-1].data.status == 'failed'
    assert 'PRIVATE' not in str(events)
