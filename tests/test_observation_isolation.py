"""Optional observations never decide execution success or control outcomes."""

from native_application_helpers import async_test

import asyncio

import pytest
from langchain_core.messages import AIMessage
from langgraph.errors import GraphInterrupt

from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.application.run_events import RunObservation
from naiyue_test_agent.agent_runtime.turn_observation import TurnObserver
from naiyue_test_agent.agent_runtime.context_operation_observation import ContextOperationObserver
from naiyue_test_agent.agent_runtime.message_observation import MessageStateObserver
from test_application_run_events import make_app
from test_async_application import StreamingToolModel
from test_web_api import web_client, request
from event_sequence import EventSequenceChecker


@pytest.mark.parametrize('event_type', ['run_started', 'model_call_started', 'model_call_finished', 'run_finished'])
@async_test
async def test_success_survives_publish_fault(monkeypatch, event_type, caplog):
    app = await make_app(monkeypatch)
    session = await app.acreate_session('A')
    publish = RunEventPublisher.publish

    def fault(self, kind, data):
        if kind == event_type:
            raise ValueError('PRIVATE_EVENT_FAULT')
        return publish(self, kind, data)

    monkeypatch.setattr(RunEventPublisher, 'publish', fault)
    result = await app.arun(session, [('user', 'probe')])
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert app._sessions.require(session).describe_session()['busy'] is False
    assert 'PRIVATE_EVENT_FAULT' not in caplog.text
    assert any('publish_failed' in item.message or 'observation_failed' in item.message
               for item in caplog.records)


@pytest.mark.parametrize('owner,operation', [
    (TurnObserver, 'begin'), (TurnObserver, 'mark_ready'),
    (ContextOperationObserver, 'model_call_started'), (ContextOperationObserver, 'finalize'),
    (MessageStateObserver, 'observe'), (TurnObserver, 'snapshot'),
])
@async_test
async def test_observer_fault_does_not_change_result(monkeypatch, owner, operation, caplog):
    app = await make_app(monkeypatch)
    session = await app.acreate_session('A')

    def fault(*_args, **_kwargs):
        raise ValueError('PRIVATE_OBSERVER_FAULT')

    monkeypatch.setattr(owner, operation, fault)
    result = await app.arun(session, [('user', 'probe')])
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert app._sessions.require(session).describe_session()['busy'] is False
    assert 'observation_failed' in caplog.text and 'PRIVATE_OBSERVER_FAULT' not in caplog.text


@async_test
async def test_listener_cannot_interrupt_the_graph(monkeypatch, caplog):
    app = await make_app(monkeypatch)
    received = []

    def faulty(_event):
        raise GraphInterrupt(())

    app.subscribe_events(faulty)
    app.subscribe_events(received.append)
    result = await app.arun(await app.acreate_session('A'), [('user', 'probe')])
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert received[-1].data.status == 'completed'
    assert 'event_observer_failed' in caplog.text


@pytest.mark.parametrize('method,error', [
    ('failed', RuntimeError('original failure')),
    ('cancelled', asyncio.CancelledError()),
])
def test_run_observation_does_not_swallow_base_exception_from_observer(monkeypatch, method, error):
    observation = RunObservation(RunEventPublisher('s', 'r', ()), 'message')

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(observation.turns, 'fail', interrupt)
    with pytest.raises(KeyboardInterrupt):
        getattr(observation, method)(error)


@async_test
async def test_sequence_checker_is_asserted_outside_delivery(monkeypatch):
    app = await make_app(monkeypatch)
    events = []
    app.subscribe_events(events.append)
    await app.arun(await app.acreate_session('A'), [('user', 'probe')])
    EventSequenceChecker.assert_valid(events)
    with pytest.raises(AssertionError, match='not contiguous'):
        EventSequenceChecker.assert_valid(events[1:])


@async_test
async def test_payload_construction_failure_is_optional(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime import call_observation
    app = await make_app(monkeypatch)

    def fault(*_args):
        raise ValueError('PRIVATE_PAYLOAD_FAULT')

    monkeypatch.setattr(call_observation, 'ModelCallStartedData', fault)
    result = await app.arun(await app.acreate_session('A'), [('user', 'probe')])
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert 'publish_failed' in caplog.text and 'PRIVATE_PAYLOAD_FAULT' not in caplog.text


def test_reentrant_notifications_preserve_listener_order():
    from naiyue_test_agent.agent_runtime.events import RunStartedData, RunFinishedData
    observed = []
    publisher = None

    def first(event):
        observed.append(('first', event.sequence))
        if event.sequence == 1:
            publisher.publish('run_finished', RunFinishedData('completed'))

    def second(event):
        observed.append(('second', event.sequence))

    publisher = RunEventPublisher('s', 'r', (first, second))
    publisher.publish('run_started', RunStartedData('message'))
    assert observed == [('first', 1), ('second', 1), ('first', 2), ('second', 2)]


@pytest.mark.parametrize('missing', ['run_started', 'run_finished', 'all'])
@async_test
async def test_web_settles_after_missing_notifications(monkeypatch, tmp_path, missing):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='answer'), AIMessage(content='answer')])
        options = {'max_receipts': 1} if missing == 'all' else {}
        async with web_client(monkeypatch, tmp_path, model, **options) as (client, web):
            session = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            publish = RunEventPublisher.publish

            def drop(self, kind, data):
                if missing == 'all' or kind == missing:
                    return None
                return publish(self, kind, data)

            monkeypatch.setattr(RunEventPublisher, 'publish', drop)
            body = request('question')
            response = await asyncio.wait_for(client.post(
                f'/api/sessions/{session}/runs', json=body), 5)
            if missing == 'all':
                assert response.status_code == 503
                assert response.json()['error']['code'] == 'run_observation_unavailable'
                replay = await asyncio.wait_for(client.post(
                    f'/api/sessions/{session}/runs', json=body), 5)
                assert replay.status_code == 503
                assert replay.json()['error']['code'] == 'run_observation_unavailable'
                conflict = await client.post(f'/api/sessions/{session}/runs',
                    json={**body, 'text': 'different'})
                assert conflict.status_code == 409
                fresh = await client.post(f'/api/sessions/{session}/runs', json=request('new'))
                assert fresh.status_code == 409
                assert fresh.json()['error']['code'] == 'request_window_full'
            else:
                assert response.status_code == 202
                run_id = response.json()['run_id']
                await web.state.delivery.wait(run_id)
                view = (await client.get(f'/api/runs/{run_id}')).json()
                assert view['status'] == 'completed' and view['incomplete'] is True
                assert view['messages'][-1]['content'] == 'answer'
            state = (await client.get(f'/api/sessions/{session}')).json()
            assert state['busy'] is False and state['messages'][-1]['content'] == 'answer'
            assert len(model.seen) == 1
    await scenario()


@pytest.mark.parametrize('failure', [False, True])
@async_test
async def test_fast_web_completion_does_not_race_queued_start_callback(failure):
    from naiyue_test_agent.web.settings import WebSettings
    from uuid import uuid4
    from naiyue_test_agent.web.run_delivery import RunDelivery
    from naiyue_test_agent.web.schemas import MessageInput
    from naiyue_test_agent.agent_runtime.events import RunStartedData, RunFinishedData

    class ImmediateApplication:
        def __init__(self):
            from naiyue_test_agent.application.run_events import ApplicationEventSubscriptions
            self.events = ApplicationEventSubscriptions()

        def subscribe_events(self, handler, *, session_id=None):
            return self.events.subscribe(handler, session_id=session_id)

        async def arun(self, session, messages):
            pub = self.events.publisher(session, 'actual-run')
            pub.publish('run_started', RunStartedData('message'))
            if failure:
                raise ValueError('PRIVATE_FAST_FAILURE')
            pub.publish('run_finished', RunFinishedData('completed'))
            return {'messages': [*messages, AIMessage(content='answer')]}

    async def scenario():
        settings = WebSettings(database_path='unused', frontend_dist='unused', event_capacity=20,
                               max_frame_bytes=100000, max_receipts=20, retained_runs=10)
        delivery = RunDelivery(ImmediateApplication(), settings)
        response = await delivery.start('s', MessageInput(request_id=str(uuid4()), text='q'), [])
        assert response['run_id'] == 'actual-run'
        await delivery.wait('actual-run')
        assert delivery.get('actual-run').view()['status'] == ('unconfirmed' if failure else 'completed')
        assert len(delivery.receipts) == 1 and not delivery.pending_receipts
    await scenario()
