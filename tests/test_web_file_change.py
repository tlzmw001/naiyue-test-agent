"""Change details come from saved artifacts, not the current workspace file."""

from uuid import uuid4
from langchain_core.messages import AIMessage
from native_application_helpers import async_test
from test_web_api import web_client
from test_application_run_events import ToolModel


@async_test
async def test_saved_change_and_patch_are_scope_checked(monkeypatch, tmp_path):
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'id': 'w', 'name': 'write', 'args': {'path': 'a', 'content': 'new\n'}}]), AIMessage(content='done')])
    async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path) as (client, web):
        created = await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})
        sid = created.json()['session_id']
        app = web.state.service.application
        await app.sessions.select_tools(sid, ['write'])
        sent = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'write'})
        await web.state.delivery.wait(sent.json()['run_id'])
        rows = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages')).json()['messages']
        result = next(row for row in rows if row['type'] == 'tool')
        assert result['file_change']['operation'] == 'write'
        path = f'/api/v1/scopes/chats/sessions/{sid}/messages/{result["sequence"]}'
        detail = await client.get(path + '/change', params={'revision': result['revision']})
        assert detail.status_code == 200 and '+new' in detail.json()['diff']
        patch = await client.get(path + '/patch', params={'revision': result['revision']})
        assert patch.status_code == 200 and '+new' in patch.text
        assert 'attachment' in patch.headers['content-disposition']
        stale = await client.get(path + '/change', params={'revision': result['revision'] + 1})
        assert stale.status_code != 200


@async_test
async def test_large_saved_change_keeps_bounded_history_entry(monkeypatch, tmp_path):
    text = 'line with actual content\n' * 1000
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'id': 'w', 'name': 'write', 'args': {'path': 'large', 'content': text}}]), AIMessage(content='done')])
    async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path,
                          history_message_bytes=2048, history_preview_chars=64) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        run = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'write'})
        await web.state.delivery.wait(run.json()['run_id'])
        rows = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages')).json()['messages']
        row = next(row for row in rows if row['type'] == 'tool')
        assert row['content_truncated']
        assert row['file_change']['operation'] == 'write' and row['file_change']['detail_available']
        assert row['file_change']['diff_truncated'] and not row['file_change']['patch_omitted']
        response = await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages/{row["sequence"]}/change',
                                    params={'revision': row['revision']})
        assert response.status_code == 200 and '+line with actual content' in response.json()['diff']
