"""HTTP scope and paused-root stops preserve admission, correlation and one producer."""

import asyncio
from uuid import uuid4
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from naiyue_test_agent.settings import AppSettings
from test_async_application import call
from test_subagent_execution import ChildModel, role
from test_web_api import web_client, request
from test_web_tools import start, approval_request


def test_http_stop_only_a_while_b_waits_in_model_and_reject_stale_child_approval(monkeypatch, tmp_path):
    async def scenario():
        workspace = tmp_path / 'workspace'; workspace.mkdir()
        outside = tmp_path / 'outside'; outside.write_text('never read')
        entered, release, notified = asyncio.Event(), asyncio.Event(), asyncio.Event()
        model = ChildModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'subagent', 'args': {'agent': 'tester', 'task': task}, 'id': 'outer-' + task} for task in ('A', 'B')]),
            call('read', {'path': str(outside)}, 'inner'), AIMessage(content='B report'), AIMessage(content='root done')])
        async def hold_b(messages):
            if any(isinstance(m, HumanMessage) and m.content == 'B' for m in messages):
                entered.set(); await release.wait()
        model.on_request = hold_b
        runtime = AppSettings(subagents=(role(model_id=None, tool_names=('read',)),))
        async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace, runtime_settings=runtime) as (client, web):
            app = web.state.service.application
            app.subscribe_events(lambda event: notified.set() if event.source and event.event_type == 'approval_requested' else None)
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('two'))).json()['run_id']
            try:
                await asyncio.wait_for(notified.wait(), 5); await asyncio.wait_for(entered.wait(), 5)
                batch, = (await client.get(f'/api/sessions/{sid}')).json()['approval_batches']
                body = {'request_id': str(uuid4()), 'kind': 'stop', 'agent_path': batch['agent_path']}
                endpoint = f'/api/runs/{run}/controls'
                assert (await client.post(endpoint, json=body, headers={'Authorization': ''})).status_code == 401
                assert (await client.post(endpoint, json={**body, 'agent_path': 'root/tester/other'})).status_code == 409
                assert (await client.post(endpoint, json={**body, 'kind': 'steering', 'text': 'x'})).status_code == 422
                receipt = await client.post(endpoint, json=body)
                assert receipt.status_code == 202 and receipt.json()['retained'] == []
                assert (await client.post(endpoint, json=body)).json() == receipt.json()
                rejected = await client.post(f'/api/sessions/{sid}/approvals', json=approval_request(batch, ['approve']))
                assert rejected.status_code == 409
                view = (await client.get(f'/api/runs/{run}')).json()
                assert view['status'] == 'running' and not view['done'] and not view['stop_requested']
                assert len(web.state.delivery.runs) == 1
                release.set()
                await asyncio.wait_for(web.state.delivery.wait(run), 5)
                final = (await client.get(f'/api/runs/{run}')).json()
                assert final['status'] == 'completed' and final['agents'][batch['agent_path']]['status'] == 'stopped'
                reports = {m['tool_call_id']: m for m in final['messages'] if m['type'] == 'tool'}
                assert reports['outer-A']['status'] == 'error' and reports['outer-B']['content'] == 'B report'
                assert (await client.post(endpoint, json={**body, 'request_id': str(uuid4())})).status_code == 409
            finally:
                release.set()
    asyncio.run(scenario())


def test_http_paused_root_stop_new_run_idempotence_and_zero_tool_execution(monkeypatch, tmp_path):
    async def scenario():
        workspace = tmp_path / 'workspace'; workspace.mkdir()
        outside = tmp_path / 'outside'; outside.write_text('not approved')
        model = ChildModel(replies=[call('read', {'path': str(outside)}, 'inner')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace) as (client, web):
            sid, origin = await start(client)
            batch, = (await client.get(f'/api/sessions/{sid}')).json()['approval_batches']
            body = {'request_id': str(uuid4()), 'origin_run_id': origin, 'approval_batch_id': batch['approval_batch_id']}
            endpoint = f'/api/sessions/{sid}/approvals/stop'
            assert (await client.post(endpoint, json=body, headers={'Authorization': ''})).status_code == 401
            assert (await client.post(endpoint, json={**body, 'approval_batch_id': 'stale'})).status_code == 409
            receipt = await client.post(endpoint, json=body)
            assert receipt.status_code == 202 and receipt.json()['run_id'] != origin
            await client.get(f'/api/runs/{receipt.json()["run_id"]}/events')
            assert (await client.post(endpoint, json=body)).json() == receipt.json()
            assert (await client.post(endpoint, json={**body, 'request_id': str(uuid4())})).status_code == 409
            final = (await client.get(f'/api/sessions/{sid}')).json()
            assert final['live']['status'] == 'stopped' and final['approval_batches'] == [] and final['can_run']
            message, = [row for row in final['messages'] if row['type'] == 'tool']
            assert 'stopped' in message['content'] and 'not approved' not in message['content']
            assert len(model.seen) == 1
    asyncio.run(scenario())
