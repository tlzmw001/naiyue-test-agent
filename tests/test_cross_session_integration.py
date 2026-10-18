"""Combined root/child approvals, concurrent commits and archive reopening."""

import asyncio
from pathlib import Path
from threading import Event
from uuid import uuid4

from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent.tools import ToolSettings
from naiyue_test_agent.tools import write as write_module
from test_async_application import call
from test_cross_session_concurrency import SessionModel, gated
from test_subagent_execution import role
from test_web_api import web_client, request
from test_web_cross_session_concurrency import sessions, runtime_events
from test_web_tools import approval_request


@async_test
async def test_root_child_approvals_capacity_and_concurrent_save_reopen(monkeypatch, tmp_path, caplog):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'approved.txt'
    model, commits = SessionModel(replies=[]), []
    settings = ToolSettings(max_active_runs=2, subagents=(role(model_id=None, tool_names=('read',)),))
    replace = write_module.os.replace
    def record(source, target):
        result = replace(source, target)
        if Path(target) == outside:
            commits.append('committed')
        return result
    monkeypatch.setattr(write_module.os, 'replace', record)
    saved, release_save = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace,
                          runtime_settings=settings) as (client, web):
        a, b, c, d = await sessions(client, 4)
        model.responses[a] = [call('write', {'path': str(outside), 'content': 'ONCE'}, 'write-a'),
            call('subagent', {'agent': 'tester', 'task': 'child-A'}, 'delegate'), AIMessage(content='A complete')]
        model.responses['child-A'] = [call('read', {'path': str(outside)}, 'read-child'), AIMessage(content='child report')]
        backend, delivery = web.state.service.application, web.state.delivery
        entered_b, release_b = gated(model, b)
        entered_c, release_c = gated(model, c)
        child_approval = asyncio.Event()
        def observe(event):
            if event.session_id == a and event.source and event.event_type == 'approval_requested':
                child_approval.set()
        unsubscribe = backend.subscribe_events(observe)
        try:
            rb = (await client.post(f'/api/sessions/{b}/runs', json=request(b))).json()['run_id']
            await asyncio.wait_for(entered_b.wait(), 5)
            ra = (await client.post(f'/api/sessions/{a}/runs', json=request(a))).json()['run_id']
            await delivery.wait(ra)
            batch, = (await client.get(f'/api/sessions/{a}')).json()['approval_batches']
            assert batch['kind'] == 'paused_root' and not outside.exists() and commits == []
            assert set(delivery.active_by_session) == {b}
            rc = (await client.post(f'/api/sessions/{c}/runs', json=request(c))).json()['run_id']
            await asyncio.wait_for(entered_c.wait(), 5)
            approval = approval_request(batch, ['approve'])
            refused = await client.post(f'/api/sessions/{a}/approvals', json=approval)
            assert refused.status_code == 409 and refused.json()['error']['code'] == 'run_capacity_exceeded'
            assert len(model.requests[a]) == 1 and commits == []
            assert (await client.get(f'/api/sessions/{a}')).json()['approval_batches'] == [batch]
            for kind in ('steering', 'follow_up'):
                assert (await client.post(f'/api/runs/{rc}/controls', json={
                    'request_id': str(uuid4()), 'kind': kind, 'text': 'C ' + kind})).status_code == 202
            stopped = await client.post(f'/api/runs/{rc}/controls', json={'request_id': str(uuid4()), 'kind': 'stop'})
            assert stopped.json()['retained'] == ['C steering', 'C follow_up']
            await delivery.wait(rc)
            resumed = await client.post(f'/api/sessions/{a}/approvals', json=approval)
            assert resumed.status_code == 202
            resumed_id = resumed.json()['run_id']
            assert resumed_id != ra
            await asyncio.wait_for(child_approval.wait(), 5)
            child_batch, = (await client.get(f'/api/sessions/{a}')).json()['approval_batches']
            assert child_batch['kind'] == 'live_child' and child_batch['origin_run_id'] == resumed_id
            assert commits == ['committed'] and outside.read_text() == 'ONCE'
            assert set(delivery.active_by_session) == {a, b}
            # No room for D, including no persistence/model/tool effects.
            assert (await client.post(f'/api/sessions/{d}/runs', json=request(d))).status_code == 409
            assert d not in model.requests and backend._storage.get(d)['run_id'] is None
            checkpoint = await backend._agent.aget_state({'configurable': {
                'thread_id': backend._sessions.get_thread_id(d)}})
            assert not checkpoint.values
            finish = backend._storage.finish_run
            def hold(sid, *args, **kwargs):
                if sid == a:
                    loop.call_soon_threadsafe(saved.set)
                    assert release_save.wait(10)
                return finish(sid, *args, **kwargs)
            monkeypatch.setattr(backend._storage, 'finish_run', hold)
            child_body = approval_request(child_batch, ['approve'])
            approved = await client.post(f'/api/sessions/{a}/approvals', json=child_body)
            assert approved.status_code == 202 and approved.json()['run_id'] == resumed_id
            await asyncio.wait_for(saved.wait(), 5)
            assert backend._sessions.describe_session(a)['busy']
            assert not backend._sessions._sessions[a]._run.controls_open
            release_b.set()
            await delivery.wait(rb)
            assert backend._storage.get(b)['state'] == 'ready'
            assert set(backend._persistence._active) == {a}
            assert not delivery.get(resumed_id).task.done()
            release_save.set()
            await delivery.wait(resumed_id)
            assert (await client.post(f'/api/sessions/{a}/approvals', json=child_body)).json() == approved.json()
            assert (await client.post(f'/api/sessions/{a}/approvals', json=approval)).json() == resumed.json()
            assert delivery.active_by_session == {} and backend._operations == {}
            assert len(model.requests[a]) == 3 and len(model.requests['child-A']) == 2
            assert len(model.requests[b]) == len(model.requests[c]) == 1
            for sid, rid in ((a, resumed_id), (b, rb), (c, rc)):
                events = runtime_events(await client.get(f'/api/runs/{rid}/events'))
                assert {event['session_id'] for event in events} == {sid}
                assert events[-1]['event_type'] == 'run_finished'
            assert [m['content'] for m in (await client.get(f'/api/sessions/{a}')).json()['messages']
                    if m['type'] == 'tool'] == ['write created 4 bytes', 'child report']
        finally:
            release_b.set()
            release_c.set()
            release_save.set()
            unsubscribe()
    calls_before = {key: len(value) for key, value in model.requests.items()}
    async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace,
                          runtime_settings=settings) as (client, web):
        for sid in (a, b, c):
            assert (await client.post(f'/api/sessions/{sid}/prepare')).status_code == 200
            value = (await client.get(f'/api/sessions/{sid}')).json()
            assert not value['busy'] and value['can_run']
        children = (await client.get(f'/api/sessions/{a}/subagents')).json()
        child, = children
        assert child['status'] == 'completed'
        detail = (await client.get(f'/api/sessions/{a}/subagents/{child["agent_id"]}')).json()
        assert [m['content'] for m in detail['messages'] if m['type'] == 'tool'] == ['ONCE']
        assert (await client.get(f'/api/sessions/{b}/subagents')).json() == []
        assert calls_before == {key: len(value) for key, value in model.requests.items()}
        assert commits == ['committed'] and outside.read_text() == 'ONCE'
    assert not any(word in caplog.text for word in ('observation_failed', 'run_event_publish_failed'))


@async_test
async def test_real_write_busy_in_shared_workspace_does_not_block_other_target(monkeypatch, tmp_path, caplog):
    model, committed = SessionModel(replies=[]), []
    entered, release = asyncio.Event(), Event()
    loop, replace = asyncio.get_running_loop(), write_module.os.replace
    def hold(source, target):
        if Path(target).name == 'shared.txt':
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
        result = replace(source, target)
        committed.append(Path(target).name)
        return result
    monkeypatch.setattr(write_module.os, 'replace', hold)
    async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path,
                          runtime_settings=ToolSettings(max_active_runs=3)) as (client, web):
        a, b, c = await sessions(client, 3)
        for sid, path, content in ((a, 'shared.txt', 'OWNER'), (b, 'shared.txt', 'LOSER'), (c, 'other.txt', 'OTHER')):
            model.responses[sid] = [call('write', {'path': path, 'content': content}, sid), AIMessage(content='finished ' + sid)]
        delivery = web.state.delivery
        try:
            ra = (await client.post(f'/api/sessions/{a}/runs', json=request(a))).json()['run_id']
            await asyncio.wait_for(entered.wait(), 5)
            for sid in (b, c):
                rid = (await client.post(f'/api/sessions/{sid}/runs', json=request(sid))).json()['run_id']
                await delivery.wait(rid)
            busy, = [m for m in (await client.get(f'/api/sessions/{b}')).json()['messages'] if m['type'] == 'tool']
            assert busy['status'] == 'error' and 'busy' in busy['content']
            assert committed == ['other.txt'] and (tmp_path / 'other.txt').read_text() == 'OTHER'
            assert set(delivery.active_by_session) == {a}
            release.set()
            await delivery.wait(ra)
            assert committed == ['other.txt', 'shared.txt'] and (tmp_path / 'shared.txt').read_text() == 'OWNER'
            assert all(len(model.requests[sid]) == 2 for sid in (a, b, c))
        finally:
            release.set()
    assert not any(word in caplog.text for word in ('observation_failed', 'run_event_publish_failed'))
