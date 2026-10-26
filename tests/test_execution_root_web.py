"""HTTP keeps duplicate receipts and maps a new execution's precise failure."""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel
from test_execution_admission import deny_execution_root
from test_web_api import web_client, request


@pytest.mark.parametrize('reason', ['missing', 'unreadable'])
def test_http_root_loss_keeps_history_and_receipt_but_rejects_new_execution(monkeypatch, tmp_path, reason):
    async def scenario():
        root = tmp_path / 'execution'
        root.mkdir()
        model = StreamingToolModel(replies=[AIMessage(content='saved answer')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, server):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            body = request('save')
            started = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=body)
            assert started.status_code == 202
            run = started.json()['run_id']
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            if reason == 'missing':
                root.rename(tmp_path / 'removed')
            else:
                deny_execution_root(monkeypatch, root)
            status = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/prepare')).json()
            assert status['can_run'] is False
            assert status['execution_root_error'] == {'code': 'execution_root_unavailable', 'reason': reason}
            assert any(message['content'] == 'saved answer' for message in status['messages'])
            duplicate = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=body)
            assert duplicate.status_code == 202 and duplicate.json() == started.json()
            denied = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('new'))
            assert denied.status_code == 409
            assert denied.json() == {'error': {'code': 'execution_root_unavailable', 'reason': reason}}
            assert len(model.seen) == 1
            assert len(server.state.delivery.runs) == 1
    asyncio.run(scenario())
