"""One native Web/SQLite flow combines compaction, child approval and tail replacement."""

import asyncio
from uuid import uuid4
from langchain_core.messages import AIMessage
from native_application_helpers import async_test
from naiyue_test_agent.settings import AppSettings
from test_async_application import call
from test_manual_compaction import compact_model
from test_subagent_execution import ChildModel, role
from test_web_api import web_client
from test_web_tools import approval_request


async def finish_run(client, web, sid, text):
    response = await client.post(f'/api/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': text})
    assert response.status_code == 202
    await web.state.delivery.wait(response.json()['run_id'])


async def approve_child(client, web, sid, run_id):
    stream = web.state.delivery.get(run_id).stream(0)
    try:
        while True:
            frame = await asyncio.wait_for(anext(stream), 5)
            if frame['data']['event_type'] == 'approval_requested':
                break
        view = (await client.get(f'/api/sessions/{sid}')).json()
        batch, = view['approval_batches']
        assert batch['kind'] == 'live_child'
        assert (await client.post(f'/api/sessions/{sid}/approvals', json=approval_request(batch, ['approve']))).status_code == 202
    finally:
        await stream.aclose()
    await web.state.delivery.wait(run_id)


@async_test
async def test_compacted_archive_child_approval_regeneration_and_two_reopens(monkeypatch, tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('real child input')
    long_answer = 'earlier detail ' * 500
    replies = [AIMessage(content=long_answer), AIMessage(content='recent answer'), AIMessage(content='Earlier work was completed.'),
        call('subagent', {'agent': 'tester', 'task': 'read outside'}, 'outer'), call('read', {'path': str(outside)}, 'inner'),
        AIMessage(content='old child'), AIMessage(content='old root'),
        call('subagent', {'agent': 'tester', 'task': 'read outside'}, 'outer'), call('read', {'path': str(outside)}, 'inner'),
        AIMessage(content='new child'), AIMessage(content=[{'type': 'reasoning', 'reasoning': 'actual visible reasoning'},
                                                         {'type': 'text', 'text': 'new root'}])]
    for message in replies:
        message.usage_metadata = {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}
    model = ChildModel(replies=replies, metadata=compact_model().metadata)
    settings = AppSettings(subagents=(role(tool_names=('read',), model_id=None),))
    options = dict(workspace_root=work, runtime_settings=settings)
    async with web_client(monkeypatch, tmp_path, model, **options) as (client, web):
        sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
        await finish_run(client, web, sid, 'old task')
        await finish_run(client, web, sid, 'recent task')
        response = await client.post(f'/api/sessions/{sid}/compact', json={'request_id': str(uuid4())})
        await web.state.delivery.wait(response.json()['run_id'])
        assert len(model.seen) == 3
        assert (await web.state.service.application.aget_messages(sid))[0].additional_kwargs['naiyue_compaction_summary']
        saved_prefix = [(r['message_id'], r['body_json']) for r in web.state.service.application._storage.database.read_rows(
            "SELECT message_id,body_json FROM naiyue_chat_messages WHERE session_id=? AND kind='message' ORDER BY sequence", (sid,))]
    async with web_client(monkeypatch, tmp_path, model, **options) as (client, web):
        assert (await client.post(f'/api/sessions/{sid}/prepare')).status_code == 200
        assert len(model.seen) == 3
        old = await client.post(f'/api/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'delegate'})
        await approve_child(client, web, sid, old.json()['run_id'])
        old_child = (await client.get(f'/api/sessions/{sid}/subagents')).json()[0]['agent_id']
        view = (await client.get(f'/api/sessions/{sid}')).json()
        regen = await client.post(f'/api/sessions/{sid}/regenerate', json={
            'request_id': str(uuid4()), 'expected_reply_id': view['last_reply_id'], 'expected_conversation_revision': 0})
        assert regen.status_code == 200
        await approve_child(client, web, sid, regen.json()['run_id'])
        assert len(model.seen) == 11
        app = web.state.service.application
        root = app._storage.sessions.get(sid)['thread_id']
        archived = app._storage.database.read_rows("SELECT message_id,body_json FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND kind='message' ORDER BY sequence", (sid, root))
        assert [(r['message_id'], r['body_json']) for r in archived[:4]] == saved_prefix
        assert (await client.get(f'/api/sessions/{sid}/subagents/{old_child}')).status_code == 404
        assert any(m.additional_kwargs.get('naiyue_compaction_summary') for m in await app.aget_messages(sid))
        usage = await app.aget_model_usage(sid, model_limit=10)
        assert usage['recorded_calls'] == 11
        assert usage['metrics']['input_tokens']['known_sum'] == '110'
        assert usage['metrics']['output_tokens']['known_sum'] == '55'
        assert {r['id'] for r in usage['activities']} == {'root_agent', 'child_agent', 'summary'}
    async with web_client(monkeypatch, tmp_path, model, **options) as (client, web):
        assert (await client.post(f'/api/sessions/{sid}/prepare')).status_code == 200
        app = web.state.service.application
        current = await app.aget_messages(sid)
        assert current[-1].content[1]['text'] == 'new root' and len(model.seen) == 11
        assert (await client.get(f'/api/sessions/{sid}')).json()['conversation_revision'] == 1
        page = await app.aget_chat_page(sid, limit=100, body_bytes=40000, preview_chars=100)
        assert len(page['records']) == 8 and page['has_compaction']
        assert (await app.aget_model_usage(sid, model_limit=10))['recorded_calls'] == 11
