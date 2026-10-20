"""Authenticated Web and child streams carry the same safe MCP message facts."""

import asyncio
import json

from langchain_core.messages import AIMessage

from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls
from test_mcp_connections import stdio_settings
from test_mcp_subagents import MCPChildModel, make_child_app
from test_web_api import request, web_client


def test_web_selection_approval_sse_and_paged_history_agree(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'web.jsonl'
        settings = AppSettings.model_validate({'mcp': {'servers': {'local': stdio_settings(path, 'tools',
            allowed_tools=['echo'], max_result_bytes=512).model_dump()}}})
        model = StreamingToolModel(replies=[call(NAME, {'text': 'large'}), AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path, runtime_settings=settings) as (client, web):
            assert (await client.get('/api/session-controls', headers={'Authorization': ''})).status_code == 401
            catalog = (await client.get('/api/session-controls')).json()
            entry = next(row for row in catalog['tools'] if row['name'] == NAME)
            assert entry['mcp_source'] == {'server_id': 'local', 'remote_tool': 'echo', 'transport': 'stdio'}
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/sessions/{sid}'
            assert (await client.patch(endpoint + '/tools', json={'active_tools': [NAME]})).status_code == 200
            run = (await client.post(endpoint + '/runs', json=request('large'))).json()['run_id']
            await web.state.delivery.wait(run)
            view = (await client.get(endpoint)).json()
            batch = view['approval_batches'][0]
            row = batch['items'][0]
            assert row['remember_scope'] == 'tool' and row['mcp_source']['remote_tool'] == 'echo'
            assert not calls(path)
            response = await client.post(endpoint + '/approvals', json={'request_id': request('')['request_id'],
                'origin_run_id': batch['origin_run_id'],
                'approval_batch_id': batch['approval_batch_id'],
                'decisions': [{'approval_id': row['approval_id'], 'decision': 'approve'}], 'remember_approved': []})
            assert response.status_code == 202, response.text
            run = response.json()['run_id']
            await web.state.delivery.wait(run)
            stream = await client.get(f'/api/runs/{run}/events')
            view = (await client.get(endpoint)).json()
            message = next(m for m in view['messages'] if m['type'] == 'tool')
            result = message['mcp_result']
            assert result['truncated'] and result['archive_complete']
            assert 'mcp_result' in stream.text and 'archive_complete' in stream.text
            page = (await client.get(endpoint + '/messages')).json()
            saved = next(m for m in page['messages'] if m['type'] == 'tool')
            assert saved['mcp_result'] == result
            assert len(calls(path)) == 1
            assert 'config_identity' not in json.dumps(catalog) + json.dumps(result)
    asyncio.run(scenario())


def test_child_stream_snapshot_has_source_result_without_root_inference(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'child.jsonl'
        child = MCPChildModel(replies=[call(NAME, {}), AIMessage(content='child done')])
        app, _ = await make_child_app(monkeypatch, path, child, mode='BypassApprovals')
        events = []; app.subscribe_events(events.append)
        try:
            sid = await app.acreate_session('A')
            await app.arun(sid, [('user', 'child')])
            rows = [json.loads(e.data.snapshot_json) for e in events
                    if e.event_type == 'message_completed' and e.source is not None]
            message = next(row for row in rows if row.get('mcp_result'))
            assert message['mcp_result']['outcome'] == 'success'
            assert message['mcp_result']['source']['server_id'] == 'local'
            assert len(calls(path)) == 1
        finally:
            await app.aclose()
    asyncio.run(scenario())
