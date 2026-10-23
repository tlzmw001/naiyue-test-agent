"""Concurrent delivery keeps recovery, saves and shutdown scoped to owners."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from native_application_helpers import async_test
from naiyue_test_agent.application.observability.run_events import ApplicationEventSubscriptions
from naiyue_test_agent.contracts.session_events import RunStartedData, RunFinishedData
from naiyue_test_agent.web.errors import WebError
from naiyue_test_agent.web.run_delivery import RunDelivery
from naiyue_test_agent.web.schemas import MessageInput
from naiyue_test_agent.web.settings import WebSettings
from test_cross_session_concurrency import SessionModel, gated
from test_session_recovery import unfinished
from test_web_api import web_client, request
from test_web_cross_session_concurrency import sessions


@async_test
async def test_running_a_allows_cold_b_recovery_and_idle_deletion(monkeypatch, tmp_path):
    cold, _, _ = await unfinished(monkeypatch, tmp_path / 'sessions.sqlite')
    model = SessionModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        a, idle = await sessions(client, 2)
        delivery = web.state.delivery
        idle_run = (await client.post(f'/api/sessions/{idle}/runs', json=request(idle))).json()['run_id']
        await delivery.wait(idle_run)
        entered, release = gated(model, a)
        ra = (await client.post(f'/api/sessions/{a}/runs', json=request(a))).json()['run_id']
        try:
            await asyncio.wait_for(entered.wait(), 5)
            history = (await client.post(f'/api/sessions/{cold}/prepare')).json()
            assert history['recovery']['kind'] == 'interrupted_execution'
            recovered = await client.post(f'/api/sessions/{cold}/recover', json={
                'checkpoint_id': history['checkpoint_id'], 'action': 'continue'})
            assert recovered.status_code == 200 and recovered.json()['can_run']
            assert cold not in model.requests
            assert (await client.delete(f'/api/sessions/{idle}')).status_code == 200
            assert (await client.get(f'/api/runs/{idle_run}')).status_code == 404
            assert (await client.delete(f'/api/sessions/{cold}')).status_code == 200
            assert set(delivery.active_by_session) == {a} and delivery.latest == {a: ra}
            assert not delivery.get(ra).task.done()
            assert (await client.delete(f'/api/sessions/{a}')).json()['error']['code'] == 'session_busy'
            assert [row['session_id'] for row in (await client.get('/api/sessions')).json()] == [a]
        finally:
            release.set()


@async_test
async def test_a_save_failure_keeps_b_active_and_its_save_healthy(monkeypatch, tmp_path, caplog):
    model = SessionModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        a, b = await sessions(client, 2)
        delivery, backend = web.state.delivery, web.state.service.application
        finish = backend._storage.finish_run
        primary = OSError('PRIVATE_SAVE')
        def fail(sid, *args, **kwargs):
            if sid == a:
                raise primary
            return finish(sid, *args, **kwargs)
        monkeypatch.setattr(backend._storage, 'finish_run', fail)
        entered, release = gated(model, b)
        rb = (await client.post(f'/api/sessions/{b}/runs', json=request(b))).json()['run_id']
        try:
            await asyncio.wait_for(entered.wait(), 5)
            ra = (await client.post(f'/api/sessions/{a}/runs', json=request(a))).json()['run_id']
            await delivery.wait(ra)
            assert delivery.get(ra).status == 'failed' and set(delivery.active_by_session) == {b}
            assert not delivery.get(rb).task.done() and set(backend._persistence._active) == {b}
            assert backend._persistence._blocked == {a}
            release.set()
            await delivery.wait(rb)
            assert delivery.get(rb).status == 'completed' and backend._storage.get(b)['state'] == 'ready'
            assert delivery.active_by_session == {} and backend._persistence._active == {}
        finally:
            release.set()
    assert 'PRIVATE_SAVE' not in caplog.text


@async_test
async def test_cancelled_http_start_keeps_a_owned_and_does_not_block_b(monkeypatch, tmp_path):
    model = SessionModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        a, b = await sessions(client, 2)
        entered, release = gated(model, a)
        request_task = None
        def cancel(event):
            if event.session_id == a and event.event_type == 'run_started':
                request_task.cancel('cancel A HTTP waiter')
        unsubscribe = web.state.service.application.subscribe_events(cancel)
        body = request(a)
        request_task = asyncio.create_task(client.post(f'/api/sessions/{a}/runs', json=body))
        try:
            with pytest.raises(asyncio.CancelledError):
                await request_task
            await asyncio.wait_for(entered.wait(), 5)
            ra = web.state.delivery.latest[a]
            b_response = await client.post(f'/api/sessions/{b}/runs', json=request(b))
            assert b_response.status_code == 202
            await web.state.delivery.wait(b_response.json()['run_id'])
            assert not web.state.delivery.get(ra).task.done()
            repeated = await client.post(f'/api/sessions/{a}/runs', json=body)
            assert repeated.json()['run_id'] == ra and len(model.requests[a]) == 1
        finally:
            release.set()
            unsubscribe()
            await asyncio.gather(request_task, return_exceptions=True)


@async_test
async def test_close_error_still_drains_all_producers_and_preserves_primary():
    subscriptions = ApplicationEventSubscriptions()
    entered, release = asyncio.Event(), asyncio.Event()
    primary = RuntimeError('PRIVATE_CLOSE')
    async def run(sid, messages):
        publisher = subscriptions.publisher(sid, 'run-' + sid)
        publisher.publish('run_started', RunStartedData('message'))
        await release.wait()
        publisher.publish('run_finished', RunFinishedData('completed'))
        return {'messages': messages}
    async def close():
        entered.set()
        raise primary
    settings = WebSettings(database_path='unused', frontend_dist='unused')
    delivery = RunDelivery(SimpleNamespace(arun=run, aclose=close, subscribe_events=subscriptions.subscribe), settings)
    for sid in ('A', 'B'):
        await delivery.start(sid, MessageInput(request_id=str(uuid4()), text=sid), [])
    jobs = tuple(delivery.active_by_session.values())
    closing = asyncio.create_task(delivery.close())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not closing.done() and all(not job.task.done() for job in jobs)
        with pytest.raises(WebError) as refused:
            await delivery.start('C', MessageInput(request_id=str(uuid4()), text='never'), [])
        assert (refused.value.code, refused.value.status) == ('session_unavailable', 503)
        release.set()
        with pytest.raises(RuntimeError) as caught:
            await closing
        assert caught.value is primary
        assert all(job.done and job.task.done() and job.status == 'completed' for job in jobs)
        assert delivery.active_by_session == {} and subscriptions._handlers == {}
    finally:
        release.set()
        await asyncio.gather(closing, return_exceptions=True)


@async_test
async def test_unsubscribe_failure_is_observable_and_does_not_retain_an_active_job(monkeypatch, tmp_path, caplog):
    model = SessionModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid, = await sessions(client, 1)
        subscriptions = web.state.service.application._sessions._events
        application_handlers = dict(subscriptions._handlers)
        assert list(application_handlers.values()) == [(None, web.state.service.application._usage.accept),
                                                      (None, web.state.service.application._diagnostics.accept)]
        subscribe = subscriptions.subscribe
        def failing_subscription(handler, **kwargs):
            unsubscribe = subscribe(handler, **kwargs)
            def fail():
                unsubscribe()
                raise ValueError('PRIVATE_UNSUBSCRIBE')
            return fail
        monkeypatch.setattr(subscriptions, 'subscribe', failing_subscription)
        rid = (await client.post(f'/api/sessions/{sid}/runs', json=request(sid))).json()['run_id']
        await web.state.delivery.wait(rid)
        assert web.state.delivery.get(rid).status == 'completed'
        assert web.state.delivery.active_by_session == {} and subscriptions._handlers == application_handlers
        assert web.state.service.application._storage.get(sid)['state'] == 'ready'
    assert subscriptions._handlers == {}
    assert 'web_event_unsubscribe_failed error_type=ValueError' in caplog.text
    assert 'PRIVATE_UNSUBSCRIBE' not in caplog.text
