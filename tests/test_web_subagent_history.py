"""Read-only Web child views preserve session ownership and saved messages."""

import asyncio
import pytest

from langchain_core.messages import AIMessage
from naiyue_test_agent.settings import AppSettings
from test_async_application import call
from test_subagent_execution import ChildModel, role
from test_web_api import web_client, request


def test_saved_child_history_after_web_restart_and_safe_read_failure(monkeypatch, tmp_path, caplog):
    async def scenario():
        workspace = tmp_path / 'workspace'
        workspace.mkdir()
        (workspace / 'test.txt').write_text('child file content')
        model = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'inspect test'}, 'outer'),
            call('read', {'path': 'test.txt'}, 'inner'), AIMessage(content='child report'), AIMessage(content='root done')])
        settings = AppSettings(subagents=(role(tool_names=('read',), model_id=None),))
        async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace, runtime_settings=settings) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('inspect'))).json()['run_id']
            await web.state.delivery.get(run).task
            listing = await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents')
            assert listing.status_code == 200
            record, = listing.json()
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}/subagents/{record["agent_id"]}'
            detail = (await client.get(endpoint)).json()
            assert detail['messages'][-1]['content'] == 'child report'
            assert 'child file content' in str(detail['messages'])
            assert detail['status'] == 'completed'
            assert (await client.get(endpoint, headers={'Authorization': ''})).status_code == 401
            assert (await client.get(endpoint, headers={'Host': 'evil.example'})).status_code == 403
            other = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            assert (await client.get(endpoint.replace(sid, other))).status_code == 404
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents/unknown')).status_code == 404
            assert len(model.seen) == 4
        empty = ChildModel(replies=[])
        async with web_client(monkeypatch, tmp_path, empty, workspace_root=workspace, runtime_settings=settings) as (client, web):
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents')).json()[0]['agent_id'] == record['agent_id']
            detail = (await client.get(endpoint)).json()
            assert detail['messages'][-1]['content'] == 'child report'
            assert 'child file content' in str(detail['messages'])
            assert detail['tool_states'] == {} and detail['available']
            backend = web.state.service.application.sessions._persistence.storage
            original = backend.conversation.read_chat_page
            def fail(session_id, thread_id, **query):
                if thread_id.startswith('subagent:'):
                    raise OSError('PRIVATE_DISK_ERROR')
                return original(session_id, thread_id, **query)
            monkeypatch.setattr(backend.conversation, 'read_chat_page', fail)
            failed = await client.get(endpoint)
            assert failed.status_code == 503 and failed.json()['error']['code'] == 'subagent_history_unavailable'
            assert 'PRIVATE_DISK_ERROR' not in failed.text + caplog.text
            assert empty.seen == []
            assert 'subagent_history_read_failed' in caplog.text
    asyncio.run(scenario())


@pytest.mark.parametrize('count', [9, 18])
def test_large_child_history_does_not_block_main_approval(monkeypatch, tmp_path, count):
    from test_web_tools import approval_request
    async def scenario():
        workspace = tmp_path / 'workspace'
        workspace.mkdir()
        (workspace / 'large.txt').write_text('a' * 64000)
        outside = tmp_path / 'outside.txt'
        outside.write_text('approved text')
        model = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'many reads'}, 'outer'),
            *(call('read', {'path': 'large.txt'}, f'inner-{i}') for i in range(count)),
            call('read', {'path': str(outside)}, 'ask'), AIMessage(content='child report'), AIMessage(content='root done')])
        settings = AppSettings(subagents=(role(tool_names=('read',), model_id=None),))
        async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace, runtime_settings=settings) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('inspect'))).json()['run_id']
            stream = web.state.delivery.get(run).stream(0)
            while True:
                frame = await asyncio.wait_for(anext(stream), 5)
                if frame['data']['event_type'] == 'approval_requested': break
            try:
                response = await client.get(f'/api/v1/scopes/chats/sessions/{sid}')
                assert response.status_code == 200
                batch, = response.json()['approval_batches']
                record, = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents')).json()
                detail = await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents/{record["agent_id"]}')
                assert detail.status_code == 200 and len(detail.content) <= web.state.delivery.settings.max_frame_bytes
                assert len(detail.json()['messages']) >= 20
                assert any(row.get('content_truncated') for row in detail.json()['messages'])
                assert 'a' * 64000 not in str(response.json()['live']['agents'])
                assert (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/approvals', json=approval_request(batch, ['approve']))).status_code == 202
                await web.state.delivery.get(run).task
                assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()['can_run']
            finally:
                await stream.aclose()
    asyncio.run(scenario())


def test_stopped_child_history_status_survives_restart(monkeypatch, tmp_path):
    from uuid import uuid4
    async def scenario():
        workspace = tmp_path / 'workspace'
        workspace.mkdir()
        outside = tmp_path / 'outside.txt'
        outside.write_text('not read')
        model = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'pending read'}, 'outer'),
            call('read', {'path': str(outside)}, 'ask'), AIMessage(content='root continued')])
        settings = AppSettings(subagents=(role(tool_names=('read',), model_id=None),))
        async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace, runtime_settings=settings) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('inspect'))).json()['run_id']
            stream = web.state.delivery.get(run).stream(0)
            while True:
                frame = await asyncio.wait_for(anext(stream), 5)
                if frame['data']['event_type'] == 'approval_requested': break
            path = frame['data']['source']['agent_path']
            assert (await client.post(f'/api/v1/scopes/chats/runs/{run}/controls', json={
                'kind': 'stop', 'request_id': str(uuid4()), 'agent_path': path})).status_code == 202
            await web.state.delivery.get(run).task
            await stream.aclose()
        async with web_client(monkeypatch, tmp_path, ChildModel(replies=[]), workspace_root=workspace, runtime_settings=settings) as (client, web):
            record, = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents')).json()
            assert record['status'] == 'stopped'
            detail = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents/{record["agent_id"]}')).json()
            assert detail['status'] == 'stopped'
    asyncio.run(scenario())
