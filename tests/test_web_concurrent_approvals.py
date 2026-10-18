"""Root and child approvals stay with their own HTTP job under full capacity."""

import asyncio
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent.tools import ToolSettings
from test_async_application import call
from test_cross_session_concurrency import SessionModel, gated
from test_subagent_execution import role
from test_web_api import web_client, request
from test_web_cross_session_concurrency import sessions, runtime_events
from test_web_tools import approval_request


@pytest.mark.parametrize('stop', [False, True])
@async_test
async def test_paused_root_at_capacity_preserves_decisions_or_can_be_stopped(monkeypatch, tmp_path, stop):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('approved content')
    model = SessionModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace,
                          runtime_settings=ToolSettings(max_active_runs=1)) as (client, web):
        a, b = await sessions(client, 2)
        model.responses[a] = [call('read', {'path': str(outside)}, 'read-a'), AIMessage(content='approved')]
        delivery, backend = web.state.delivery, web.state.service.application
        origin = (await client.post(f'/api/sessions/{a}/runs', json=request(a))).json()['run_id']
        await delivery.wait(origin)
        pending = (await client.get(f'/api/sessions/{a}')).json()
        batch, = pending['approval_batches']
        assert batch['kind'] == 'paused_root' and not pending['busy']
        entered, release = gated(model, b)
        other = (await client.post(f'/api/sessions/{b}/runs', json=request(b))).json()['run_id']
        try:
            await asyncio.wait_for(entered.wait(), 5)
            endpoint = f'/api/sessions/{a}/approvals'
            body = approval_request(batch, ['approve'])
            body['remember_approved'] = [batch['items'][0]['approval_id']]
            original = deepcopy(backend._sessions._sessions[a].snapshot_for_run()['pending'])
            refused = await client.post(endpoint, json=body)
            assert refused.status_code == 409 and refused.json()['error']['code'] == 'run_capacity_exceeded'
            assert backend._sessions._sessions[a].snapshot_for_run()['pending'] == original
            assert backend._sessions.resolve_permission_context(a)['session_allow'] == []
            assert len(model.requests[a]) == 1 and delivery.latest[a] == origin
            assert (a, body['request_id']) not in delivery.pending_receipts
            again = (await client.get(f'/api/sessions/{a}')).json()
            assert again['approval_batches'] == [batch] and again['live']['run_id'] == origin
            if stop:
                response = await client.post(endpoint + '/stop', json={
                    key: body[key] for key in ('request_id', 'origin_run_id', 'approval_batch_id')})
                assert response.status_code == 202
                await delivery.wait(response.json()['run_id'])
                final = (await client.get(f'/api/sessions/{a}')).json()
                assert final['live']['status'] == 'stopped' and final['approval_batches'] == []
                assert len(model.requests[a]) == 1
                assert next(m for m in final['messages'] if m['type'] == 'tool')['status'] == 'error'
                assert set(delivery.active_by_session) == {b} and not delivery.get(other).task.done()
            else:
                release.set()
                await delivery.wait(other)
                accepted = await client.post(endpoint, json=body)
                assert accepted.status_code == 202
                await delivery.wait(accepted.json()['run_id'])
                assert (await client.post(endpoint, json=body)).json() == accepted.json()
                assert len(model.requests[a]) == 2 and len(backend._sessions.resolve_permission_context(a)['session_allow']) == 1
                final = (await client.get(f'/api/sessions/{a}')).json()
                assert final['live']['status'] == 'completed' and final['approval_batches'] == []
                assert next(m for m in final['messages'] if m['type'] == 'tool')['content'] == 'approved content'
        finally:
            release.set()


@pytest.mark.parametrize('close', [False, True])
@async_test
async def test_child_approval_resumes_only_its_parent_or_all_jobs_close(monkeypatch, tmp_path, close):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('child private result')
    model = SessionModel(replies=[])
    settings = ToolSettings(max_active_runs=2, subagents=(role(model_id=None, tool_names=('read',)),))
    async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace,
                          runtime_settings=settings) as (client, web):
        a, b, c = await sessions(client, 3)
        model.responses[a] = [call('subagent', {'agent': 'tester', 'task': 'child-A'}, 'outer'), AIMessage(content='root A done')]
        model.responses['child-A'] = [call('read', {'path': str(outside)}, 'inner'), AIMessage(content='child report')]
        backend, delivery = web.state.service.application, web.state.delivery
        pending = asyncio.Event()
        def notify(event):
            if event.session_id == a and event.source and event.event_type == 'approval_requested':
                pending.set()
        unsubscribe = backend.subscribe_events(notify)
        entered, release = gated(model, b)
        rb = (await client.post(f'/api/sessions/{b}/runs', json=request(b))).json()['run_id']
        try:
            await asyncio.wait_for(entered.wait(), 5)
            ra = (await client.post(f'/api/sessions/{a}/runs', json=request(a))).json()['run_id']
            await asyncio.wait_for(pending.wait(), 5)
            batch, = (await client.get(f'/api/sessions/{a}')).json()['approval_batches']
            assert batch['kind'] == 'live_child' and batch['origin_run_id'] == ra
            assert (await client.post(f'/api/sessions/{c}/runs', json=request(c))).json()['error']['code'] == 'run_capacity_exceeded'
            original = delivery.get(ra)
            if close:
                await asyncio.wait_for(delivery.close(), 5)
                assert delivery.active_by_session == {} and backend._operations == {}
                assert delivery.get(ra).done and delivery.get(rb).done
                assert delivery.get(ra).status == delivery.get(rb).status == 'stopped'
                assert len(model.requests[a]) == len(model.requests['child-A']) == len(model.requests[b]) == 1
                assert all(row['state'] == 'ready' for row in backend._storage.list_sessions())
                assert tuple(backend._sessions._events._handlers.values()) == ((None, notify),)
            else:
                body = approval_request(batch, ['approve'])
                assert (await client.post(f'/api/sessions/{b}/approvals', json=body)).status_code == 409
                approved = await client.post(f'/api/sessions/{a}/approvals', json=body)
                assert approved.status_code == 202 and approved.json()['run_id'] == ra
                assert approved.json()['kind'] == 'live_child'
                assert (await client.post(f'/api/sessions/{a}/approvals', json=body)).json() == approved.json()
                await delivery.wait(ra)
                assert delivery.get(ra) is original and set(delivery.active_by_session) == {b}
                assert not delivery.get(rb).task.done()
                final = (await client.get(f'/api/sessions/{a}')).json()
                assert final['messages'][-1]['content'] == 'root A done'
                assert [m['content'] for m in final['messages'] if m['type'] == 'tool'] == ['child report']
                assert 'child private result' not in str(final['messages'])
                events = runtime_events(await client.get(f'/api/runs/{ra}/events'))
                assert {e['session_id'] for e in events} == {a}
                assert sum(e['event_type'] == 'run_started' and not e['source'] for e in events) == 1
                assert len(model.requests[a]) == len(model.requests['child-A']) == 2
        finally:
            release.set()
            unsubscribe()
