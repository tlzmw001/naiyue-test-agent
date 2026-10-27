"""Delivery may fail or detach without corrupting the execution lifecycle."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from naiyue_test_agent import application
from scripts.component_fixtures import build_component_application
from naiyue_test_agent.application.observability.async_event_stream import AsyncEventStream, EventStreamOverflow
from naiyue_test_agent.application.observability.run_events import ApplicationEventSubscriptions
from naiyue_test_agent.application.conversation.stream_projection import StreamingMessageProjection
from naiyue_test_agent.application.sessions.session_service import SessionBusyError
from naiyue_test_agent.agent_runtime.events import RunStartedData, RunFinishedData
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel, make_app
from test_async_persistent import factory


def test_stream_disconnect_keeps_owned_run_and_persistence(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        chosen = StreamingToolModel(replies=[AIMessage(content='saved')], entered=entered, release=release)
        async with factory(monkeypatch, tmp_path / 'sessions.sqlite', chosen) as app:
            subscriptions = app.host.sessions._sessions._events
            application_handlers = dict(subscriptions._handlers)
            assert list(application_handlers.values()) == [(None, app.host.observability._usage.accept), (None, app.host.observability._diagnostics.accept)]
            session = await app.host.sessions.create('A')
            iterator = app.host.runs.stream(session, [('user', 'start')])
            first = await iterator.__anext__()
            assert first.event_type == 'run_started'
            await asyncio.wait_for(entered.wait(), 5)
            producer = next(iter(app.host.runs._owner._tasks))
            await iterator.aclose()
            assert not producer.done() and app.host.sessions._sessions.require(session).describe_session()['busy']
            release.set()
            result = await producer
            assert result['messages'][-1].content == 'saved'
            assert (await app.host.sessions.list())[0]['state'] == 'ready'
            assert subscriptions._handlers == application_handlers
        assert subscriptions._handlers == {}
    asyncio.run(scenario())


def test_overflow_is_explicit_and_does_not_cancel_run(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        app = await make_app(monkeypatch, StreamingToolModel(replies=[AIMessage(content='answer')],
                                                          entered=entered, release=release))
        session = await app.host.sessions.create('A')
        application_handlers = dict(app.host.sessions._sessions._events._handlers)
        assert list(application_handlers.values()) == [(None, app.host.observability._usage.accept), (None, app.host.observability._diagnostics.accept)]
        iterator = app.host.runs.stream(session, [('user', 'start')], capacity=1)
        try:
            assert (await iterator.__anext__()).event_type == 'run_started'
            await asyncio.wait_for(entered.wait(), 5)
            producer = next(iter(app.host.runs._owner._tasks))
            with pytest.raises(EventStreamOverflow):
                await iterator.__anext__()
            assert not producer.done()
            release.set()
            await producer
            assert (await app.host.history.messages(session))[-1].content == 'answer'
            assert app.host.sessions._sessions._events._handlers == application_handlers
        finally:
            release.set()
            await iterator.aclose()
            await app.aclose()
        assert app.host.sessions._sessions._events._handlers == {}
    asyncio.run(scenario())


def test_busy_stream_rejects_without_hanging_or_observing_other_run(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        app = await make_app(monkeypatch, StreamingToolModel(replies=[AIMessage(content='answer')],
                                                          entered=entered, release=release))
        session = await app.host.sessions.create('A')
        running = asyncio.create_task(app.host.runs.start(session, [('user', 'first')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            delivered = []
            with pytest.raises(SessionBusyError):
                async for event in app.host.runs.stream(session, [('user', 'second')]):
                    delivered.append(event)
            assert delivered == []
        finally:
            release.set()
            await running
            await app.aclose()
    asyncio.run(scenario())


def test_failed_partial_output_remains_unconfirmed(monkeypatch, caplog):
    async def scenario():
        original = RuntimeError('PRIVATE_PROVIDER_ERROR')

        class BrokenStream(StreamingToolModel):
            async def _astream(self, *args, **kwargs):
                yield ChatGenerationChunk(message=AIMessageChunk(content='partial visible', id='partial'))
                raise original

        app = await make_app(monkeypatch, BrokenStream(replies=[]))
        session = await app.host.sessions.create('A')
        projection, events = StreamingMessageProjection(), []
        try:
            with pytest.raises(RuntimeError) as caught:
                async for event in app.host.runs.stream(session, [('user', 'start')]):
                    events.append(event)
                    projection(event)
            assert caught.value is original
            draft = next(row for row in projection.messages if row['id'] == 'partial')
            assert draft['content'] == 'partial visible' and draft['provisional']
            assert draft['state'] == 'unconfirmed'
            assert events[-1].data.status == 'failed'
            assert 'PRIVATE_PROVIDER_ERROR' not in caplog.text
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_thread_delivery_wakes_async_consumer_and_preserves_order():
    async def scenario():
        subscriptions = ApplicationEventSubscriptions()
        stream = AsyncEventStream(subscriptions.subscribe, 's', 'r', capacity=2)
        publisher = subscriptions.publisher('s', 'r')
        try:
            await asyncio.to_thread(publisher.publish, 'run_started', RunStartedData('message'))
            first = await asyncio.wait_for(stream.__anext__(), 5)
            await asyncio.to_thread(publisher.publish, 'run_finished', RunFinishedData('completed'))
            stream.finish()
            rest = [event async for event in stream]
            assert [first.event_type, *[e.event_type for e in rest]] == ['run_started', 'run_finished']
            assert first.sequence == 1 and rest[0].sequence == 2
        finally:
            stream.close()
        assert subscriptions._handlers == {}
    asyncio.run(scenario())


def test_two_sessions_switch_models_and_busy_is_per_session(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        a = StreamingToolModel(replies=[AIMessage(content='A')], entered=entered, release=release)
        b = StreamingToolModel(replies=[AIMessage(content='B-one'), AIMessage(content='B-two')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': a, 'B': b}))
        app = await build_component_application('unused', tools=[], system_prompt='', response_format=None)
        first, second = await app.host.sessions.create('A'), await app.host.sessions.create('B')
        running = asyncio.create_task(app.host.runs.start(first, [('user', 'first')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            with pytest.raises(SessionBusyError):
                await app.host.runs.start(first, [('user', 'busy')])
            assert (await app.host.runs.start(second, [('user', 'isolated')]))['messages'][-1].content == 'B-one'
            await app.host.sessions.select_model(first, 'B')
            release.set()
            assert (await running)['messages'][-1].content == 'A'
            result = await app.host.runs.start(first, [('user', 'switched')])
            assert [m.content for m in result['messages']] == ['first', 'A', 'switched', 'B-two']
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_recovery_view_cannot_confirm_new_partial_with_reused_message_id(monkeypatch):
    async def scenario():
        error = RuntimeError('synthetic stream failure')

        class ReusedIdModel(StreamingToolModel):
            streams: int = 0

            async def _astream(self, *args, **kwargs):
                self.streams += 1
                text = 'old answer' if self.streams == 1 else 'new partial'
                yield ChatGenerationChunk(message=AIMessageChunk(content=text, id='reused'))
                if self.streams == 2:
                    raise error

        app = await make_app(monkeypatch, ReusedIdModel(replies=[]))
        session = await app.host.sessions.create('A')
        projection = StreamingMessageProjection()
        try:
            await app.host.runs.start(session, [('user', 'first')])
            with pytest.raises(RuntimeError) as caught:
                async for event in app.host.runs.stream(session, [('user', 'second')]):
                    projection(event)
            assert caught.value is error
            draft = next(row for row in projection.messages if row['id'] == 'reused')
            assert draft['content'] == 'new partial' and draft['provisional'] is True
            assert draft['state'] == 'unconfirmed'
        finally:
            await app.aclose()
    asyncio.run(scenario())
