"""Cold approval keeps its current batch identity without reading full display history."""

import asyncio

from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel, call
from test_web_api import web_client
from test_web_tools import start, approval_request


def test_cold_approval_pairs_live_and_archived_tool_results(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'work'
        root.mkdir()
        outside = tmp_path / 'outside'
        outside.write_text('approved result')
        invocation = call('read', {'path': str(outside)}, 'read-call')
        invocation.id = 'approval-owner'
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[invocation]), workspace_root=root) as (client, web):
            sid, _ = await start(client)
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='done')], entered=entered, release=release)
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, web):
            prepared = (await client.post(f'/api/sessions/{sid}/prepare')).json()
            restored = await client.post(f'/api/sessions/{sid}/recover', json={
                'checkpoint_id': prepared['checkpoint_id'], 'action': 'restore_approval'})
            batch, = restored.json()['approval_batches']
            response = await client.post(f'/api/sessions/{sid}/approvals', json=approval_request(batch, ['approve']))
            assert response.status_code == 202
            rid = response.json()['run_id']
            try:
                await asyncio.wait_for(entered.wait(), 5)
                running = (await client.get(f'/api/runs/{rid}')).json()
                assert not any(m.get('identity_only') for m in running['messages'])
                result, = [m for m in running['messages'] if m['type'] == 'tool']
                assert result['parent_message_id'] == 'approval-owner'
                assert running['tool_states']['read-call']['batch_id'] == 'approval-owner'
            finally:
                release.set()
                await web.state.delivery.wait(rid)
            for path in (f'/api/sessions/{sid}', f'/api/sessions/{sid}/messages'):
                response = await client.get(path)
                assert response.status_code == 200
                result, = [m for m in response.json()['messages'] if m['type'] == 'tool']
                assert result['parent_message_id'] == 'approval-owner' and result['content'] == 'approved result'
                owner, = [m for m in response.json()['messages'] if m['id'] == 'approval-owner']
                assert owner['tool_calls'][0]['args'] == {'path': str(outside)}
            assert len(model.seen) == 1
    asyncio.run(scenario())
