"""HTTP retry, current-tail identity and actual job ownership stay aligned."""

from uuid import uuid4
from langchain_core.messages import AIMessage
from native_application_helpers import async_test
from test_web_api import web_client
from test_write_tool import RecordingFake


@async_test
async def test_regenerate_last_reply_replaces_archive_and_retries_one_job(monkeypatch, tmp_path):
    model = RecordingFake(responses=[AIMessage(content='old'), AIMessage(content='new')])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        first = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'task'})
        await web.state.delivery.wait(first.json()['run_id'])
        view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
        body = dict(request_id=str(uuid4()), expected_reply_id=view['last_reply_id'], expected_conversation_revision=0)
        response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/regenerate', json=body)
        assert response.status_code == 200
        run_id = response.json()['run_id']
        again = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/regenerate', json=body)
        assert again.json()['run_id'] == run_id
        await web.state.delivery.wait(run_id)
        final = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
        assert [row['content'] for row in final['messages']] == ['task', 'new']
        assert final['conversation_revision'] == 1 and len(model.seen) == 2
        stale = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/regenerate', json={**body, 'request_id': str(uuid4())})
        assert stale.status_code != 200


@async_test
async def test_repeated_write_uses_current_file_and_old_detail_scope_is_rejected(monkeypatch, tmp_path):
    replies = []
    for text in ('first', 'second'):
        replies.extend([AIMessage(content='', tool_calls=[{'name': 'write', 'id': text,
            'args': {'path': 'note', 'content': text}}]), AIMessage(content='done')])
    model = RecordingFake(responses=replies)
    async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        first = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'write'})
        await web.state.delivery.wait(first.json()['run_id'])
        view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
        old = next(row for row in view['messages'] if row['type'] == 'tool')
        run = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/regenerate', json={
            'request_id': str(uuid4()), 'expected_reply_id': view['last_reply_id'], 'expected_conversation_revision': 0})
        await web.state.delivery.wait(run.json()['run_id'])
        assert (tmp_path / 'note').read_text() == 'second' and len(model.seen) == 4
        url = f'/api/v1/scopes/chats/sessions/{sid}/messages/{old["sequence"]}/change'
        response = await client.get(url, params={'revision': 1, 'conversation_revision': 0})
        assert response.status_code != 200
        current = await client.get(url, params={'revision': 1, 'conversation_revision': 1})
        assert current.status_code == 200 and '-first' in current.json()['diff'] and '+second' in current.json()['diff']


@async_test
async def test_old_content_identity_cannot_read_replacement(monkeypatch, tmp_path):
    model = RecordingFake(responses=[AIMessage(content='old answer'), AIMessage(content='new answer')])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        sent = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'task'})
        await web.state.delivery.wait(sent.json()['run_id'])
        view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
        old = next(row for row in view['messages'] if row['type'] == 'ai')
        run = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/regenerate', json={
            'request_id': str(uuid4()), 'expected_reply_id': view['last_reply_id'], 'expected_conversation_revision': 0})
        await web.state.delivery.wait(run.json()['run_id'])
        url = f'/api/v1/scopes/chats/sessions/{sid}/messages/{old["sequence"]}/content'
        stale = await client.get(url, params={'revision': old['revision'], 'conversation_revision': 0})
        assert stale.status_code == 409
        current = await client.get(url, params={'revision': old['revision'], 'conversation_revision': 1})
        assert current.status_code == 200 and 'new answer' in current.json()['body_json_chunk']


@async_test
async def test_regeneration_keeps_earlier_child_and_replaces_tail_with_new_child_approval(monkeypatch, tmp_path):
    import asyncio
    from naiyue_test_agent.settings import AppSettings
    from test_async_application import call
    from test_subagent_execution import ChildModel, role
    from test_web_tools import approval_request
    workspace = tmp_path / 'work'
    workspace.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('approved child data')
    model = ChildModel(replies=[
        call('subagent', {'agent': 'tester', 'task': 'first'}, 'outer'), AIMessage(content='first child'), AIMessage(content='first root'),
        call('subagent', {'agent': 'tester', 'task': 'second'}, 'outer'), AIMessage(content='old child'), AIMessage(content='old root'),
        call('subagent', {'agent': 'tester', 'task': 'second'}, 'outer'), call('read', {'path': str(outside)}, 'inner'),
        AIMessage(content='new child'), AIMessage(content='new root')])
    settings = AppSettings(subagents=(role(tool_names=('read',), model_id=None),))
    async with web_client(monkeypatch, tmp_path, model, workspace_root=workspace, runtime_settings=settings) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        for text in ('one', 'two'):
            run = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': text})
            await web.state.delivery.wait(run.json()['run_id'])
        old_children = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents')).json()
        first_id, replaced_id = [c['agent_id'] for c in old_children]
        view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
        response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/regenerate', json={
            'request_id': str(uuid4()), 'expected_reply_id': view['last_reply_id'], 'expected_conversation_revision': 0})
        job = web.state.delivery.get(response.json()['run_id'])
        stream = job.stream(0)
        try:
            while True:
                frame = await asyncio.wait_for(anext(stream), 5)
                if frame['data']['event_type'] == 'approval_requested':
                    break
            pending = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
            batch, = pending['approval_batches']
            assert batch['kind'] == 'live_child' and pending['busy']
            approved = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/approvals', json=approval_request(batch, ['approve']))
            assert approved.status_code == 202
        finally:
            await stream.aclose()
        await web.state.delivery.wait(job.run_id)
        children = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents')).json()
        assert len(children) == 2 and first_id in {c['agent_id'] for c in children}
        assert replaced_id not in {c['agent_id'] for c in children}
        assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents/{replaced_id}')).status_code == 404
        assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents/{first_id}')).status_code == 200
        final = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
        assert [r['content'] for r in final['messages'] if r['type'] == 'ai' and r['content']] == ['first root', 'new root']
        assert len(model.seen) == 10 and final['conversation_revision'] == 1
