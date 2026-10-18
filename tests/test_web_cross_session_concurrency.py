"""HTTP jobs route each active root independently on real graphs and SQLite."""

import asyncio
import json
from uuid import uuid4

import pytest

from native_application_helpers import async_test
from naiyue_test_agent.tools import ToolSettings
from naiyue_test_agent.web.delivered_run import DeliveredRun
from test_cross_session_concurrency import SessionModel, gated
from test_web_api import web_client, request


async def sessions(client, count):
    return [(await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id'] for _ in range(count)]


def runtime_events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')
            and '"event_type"' in line]


@async_test
async def test_a_b_run_together_stop_and_sse_keep_their_owners(monkeypatch, tmp_path):
    model, observed = SessionModel(replies=[]), []
    observe = DeliveredRun.observe
    def record(job, event):
        observed.append((job.session_id, event.session_id))
        observe(job, event)
    monkeypatch.setattr(DeliveredRun, 'observe', record)
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        a, b = await sessions(client, 2)
        a_entered, a_release = gated(model, a)
        b_entered, b_release = gated(model, b)
        try:
            first = await client.post(f'/api/sessions/{a}/runs', json=request(a))
            assert first.status_code == 202
            await asyncio.wait_for(a_entered.wait(), 5)
            second = await client.post(f'/api/sessions/{b}/runs', json=request(b))
            assert second.status_code == 202
            await asyncio.wait_for(b_entered.wait(), 5)
            ra, rb = first.json()['run_id'], second.json()['run_id']
            delivery = web.state.delivery
            assert set(delivery.active_by_session) == {a, b}
            assert (await client.post(f'/api/sessions/{a}/runs', json=request('overlap'))).status_code == 409
            for kind, text in (('steering', 'A steer'), ('follow_up', 'A follow')):
                assert (await client.post(f'/api/runs/{ra}/controls', json={
                    'request_id': str(uuid4()), 'kind': kind, 'text': text})).status_code == 202
            stop_body = {'request_id': str(uuid4()), 'kind': 'stop'}
            stopped = await client.post(f'/api/runs/{ra}/controls', json=stop_body)
            assert stopped.json()['retained'] == ['A steer', 'A follow']
            assert (await client.post(f'/api/runs/{ra}/controls', json=stop_body)).json() == stopped.json()
            await asyncio.wait_for(delivery.wait(ra), 5)
            assert set(delivery.active_by_session) == {b} and not delivery.get(rb).task.done()
            assert (await client.get(f'/api/sessions/{b}')).json()['busy']
            b_release.set()
            await asyncio.wait_for(delivery.wait(rb), 5)
            for sid, rid, expected in ((a, ra, 'stopped'), (b, rb, 'completed')):
                view = (await client.get(f'/api/runs/{rid}')).json()
                assert view['status'] == expected and view['done']
                events = runtime_events(await client.get(f'/api/runs/{rid}/events'))
                assert events and {e['session_id'] for e in events} == {sid}
                assert {e['run_id'] for e in events} == {rid}
                assert [e['sequence'] for e in events] == list(range(1, len(events) + 1))
                assert events[-1]['event_type'] == 'run_finished'
            assert (await client.get(f'/api/sessions/{b}')).json()['messages'][-1]['content'] == 'done:' + b
            assert set(model.requests) == {a, b} and len(model.requests[a]) == len(model.requests[b]) == 1
            assert all(owner == origin for owner, origin in observed)
            assert delivery.active_by_session == {}
        finally:
            a_release.set()
            b_release.set()


@async_test
async def test_capacity_rejection_removes_only_its_pending_receipt_and_is_retryable(monkeypatch, tmp_path):
    model = SessionModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model, runtime_settings=ToolSettings(max_active_runs=1)) as (client, web):
        a, b = await sessions(client, 2)
        entered, release = gated(model, a)
        try:
            first = await client.post(f'/api/sessions/{a}/runs', json=request(a))
            await asyncio.wait_for(entered.wait(), 5)
            body = request(b)
            rejected = await client.post(f'/api/sessions/{b}/runs', json=body)
            assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'run_capacity_exceeded'
            delivery, backend = web.state.delivery, web.state.service.application
            assert b not in delivery.active_by_session and (b, body['request_id']) not in delivery.pending_receipts
            assert (b, body['request_id']) not in delivery.receipts and b not in delivery.latest
            assert b not in model.requests and not backend._sessions.describe_session(b)['busy']
            assert backend._storage.get(b)['run_id'] is None
            release.set()
            await delivery.wait(first.json()['run_id'])
            accepted = await client.post(f'/api/sessions/{b}/runs', json=body)
            assert accepted.status_code == 202
            await delivery.wait(accepted.json()['run_id'])
            assert (await client.post(f'/api/sessions/{b}/runs', json=body)).json() == accepted.json()
            assert len(model.requests[b]) == 1
            assert len(delivery.runs) == 2 and delivery.active_by_session == {}
        finally:
            release.set()


@pytest.mark.parametrize('observation', ['delayed', 'missing'])
@async_test
async def test_start_wait_outside_registration_lock_does_not_block_other_sessions(monkeypatch, tmp_path, observation):
    model = SessionModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        a, b = await sessions(client, 2)
        entered, release = gated(model, a)
        subscriptions = web.state.service.application._sessions._events
        original = subscriptions.publisher
        def publisher(sid, rid):
            result = original(sid, rid)
            if sid == a:
                result._handlers = ()
            return result
        monkeypatch.setattr(subscriptions, 'publisher', publisher)
        body = request(a)
        waiting = asyncio.create_task(client.post(f'/api/sessions/{a}/runs', json=body))
        duplicate = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert not web.state.delivery.start_lock.locked()
            duplicate = asyncio.create_task(client.post(f'/api/sessions/{a}/runs', json=body))
            other = await asyncio.wait_for(client.post(f'/api/sessions/{b}/runs', json=request(b)), 5)
            assert other.status_code == 202
            await web.state.delivery.wait(other.json()['run_id'])
            assert not waiting.done() and not duplicate.done() and len(model.requests[a]) == 1
            if observation == 'delayed':
                job = web.state.delivery.active_by_session[a]
                state = web.state.service.application._sessions.describe_session(a)
                job.run_id = state['run_id']
                job._ready(job.run_id)
                assert (await waiting).json() == (await duplicate).json()
            release.set()
            if observation == 'missing':
                assert (await waiting).status_code == (await duplicate).status_code == 503
                key = (a, body['request_id'])
                assert key in web.state.delivery.pending_receipts
                assert (await client.post(f'/api/sessions/{a}/runs', json=body)).status_code == 503
                assert len(model.requests[a]) == 1
        finally:
            release.set()
            await asyncio.gather(waiting, *([duplicate] if duplicate else []), return_exceptions=True)
