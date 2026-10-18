"""A live child approval uses the parent's existing HTTP job and event stream."""

import asyncio
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from naiyue_test_agent.tools import ToolSettings
from test_async_application import call
from test_subagent_execution import ChildModel, role
from test_web_api import web_client, request
from test_web_tools import approval_request


def test_live_child_approval_is_busy_idempotent_and_keeps_one_sse(monkeypatch, tmp_path):
    async def scenario():
        workspace = tmp_path / 'workspace'
        workspace.mkdir()
        outside = tmp_path / 'outside.txt'
        outside.write_text('CHILD_ONLY_FILE')
        model = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'read outside'}, 'outer'),
            call('read', {'path': str(outside)}, 'inner'), AIMessage(content='child report'), AIMessage(content='root done')])
        settings = ToolSettings(subagents=(role(tool_names=('read',), model_id=None),))
        async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace, runtime_settings=settings) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('user task'))).json()['run_id']
            job = web.state.delivery.get(run)
            stream = job.stream(0)
            frames = []
            while True:
                frame = await asyncio.wait_for(anext(stream), 5)
                frames.append(frame)
                if frame['data']['event_type'] == 'approval_requested': break
            view = (await client.get(f'/api/sessions/{sid}')).json()
            batch, = view['approval_batches']
            assert view['busy'] and view['live']['status'] == 'running' and not view['live']['done']
            assert batch['kind'] == 'live_child' and batch['origin_run_id'] == run
            assert len(model.seen) == 2 and not any(m['type'] == 'tool' for m in view['messages'])
            assert 'CHILD_ONLY_FILE' not in str(view['messages'])
            body = approval_request(batch, ['approve'])
            body['remember_approved'] = [batch['items'][0]['approval_id']]
            endpoint = f'/api/sessions/{sid}/approvals'
            other = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            assert (await client.post(f'/api/sessions/{other}/approvals', json=body)).status_code == 409
            assert (await client.post(endpoint, json=body, headers={'Authorization': ''})).status_code == 401
            for change in ({'origin_run_id': 'stale'}, {'approval_batch_id': 'stale'},
                           {'decisions': [{'approval_id': 'wrong-row', 'decision': 'approve'}]}):
                assert (await client.post(endpoint, json={**body, **change})).status_code == 409
            receipt = await client.post(endpoint, json=body)
            assert receipt.status_code == 202 and receipt.json() == {
                'session_id': sid, 'run_id': run, 'kind': 'live_child', 'accepted': True}
            async for frame in stream: frames.append(frame)
            assert (await client.post(endpoint, json=body)).json() == receipt.json()
            assert (await client.post(endpoint, json={**body, 'remember_approved': []})).status_code == 409
            assert (await client.post(endpoint, json={**body, 'request_id': str(uuid4())})).status_code == 409
            assert len(web.state.delivery.runs) == 1 and web.state.delivery.get(run) is job
            events = [frame['data'] for frame in frames]
            assert [event['sequence'] for event in events] == list(range(1, len(events) + 1))
            assert sum(event['event_type'] == 'run_started' and not event['source'] for event in events) == 1
            child = (await client.get(f'/api/runs/{run}')).json()['agents'][batch['agent_path']]
            assert child['status'] == 'completed'
            # Full child messages/arguments now have an independent detail-size boundary.
            detail = (await client.get(f'/api/sessions/{sid}/subagents/{child["source"]["agent_id"]}')).json()
            assert next(m for m in detail['messages'] if m.get('tool_call_id') == 'inner')['content'] == 'CHILD_ONLY_FILE'
            child_call = next(m for m in detail['messages'] if m.get('tool_calls'))['tool_calls'][0]
            assert child_call['args'] == {'path': str(outside)}
            assert child['tool_states']['inner']['outcome'] == 'success'
            assert child['tool_states']['inner']['state'] == 'finished'
            assert any(event['source'] and event['event_type'] == 'model_call_started' for event in events)
            assert any(event['source'] and event['event_type'] == 'tool_call_started' for event in events)
            final = (await client.get(f'/api/sessions/{sid}')).json()
            assert not final['busy'] and final['approval_batches'] == [] and final['live']['status'] == 'completed'
            assert [m['content'] for m in final['messages'] if m['type'] == 'tool'] == ['child report']
            assert next(m for m in model.seen[2] if isinstance(m, ToolMessage)).content == 'CHILD_ONLY_FILE'
            assert len(web.state.service.application._sessions.resolve_permission_context(sid)['session_allow']) == 1
    asyncio.run(scenario())
