"""Frame limits bound transport, while snapshots and saved messages restore display."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from test_async_application import StreamingToolModel
from test_web_api import web_client, request


@async_test
async def test_default_frame_limit_preserves_complete_snapshot_and_full_saved_body(monkeypatch, tmp_path):
    text = '正文😀\n' * 110000
    model = StreamingToolModel(replies=[AIMessage(id='large', content=text,
        response_metadata={'finish_reason': 'stop'})])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        receipt = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('large answer'))).json()
        rid = receipt['run_id']
        await web.state.delivery.wait(rid)
        job = web.state.delivery.get(rid)
        limit = job.settings.max_frame_bytes
        assert limit == 1024 * 1024 and len(text.encode()) > limit
        assert job.status == 'completed' and job.done
        assert not job.view()['incomplete']
        assert job.view()['messages'][-1]['provisional'] is False
        for _ in range(2):
            response = await client.get(f'/api/v1/scopes/chats/sessions/{sid}')
            assert response.status_code == 200 and len(response.content) <= limit
            row = response.json()['messages'][-1]
            assert row['id'] == 'large' and row['state'] == 'complete' and row['provisional'] is False
            assert row['content_truncated'] and row['detail']
        result, offset = [], 0
        while True:
            response = await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages/{row["sequence"]}/content',
                params={'revision': row['revision'], 'offset': offset})
            assert response.status_code == 200 and len(response.content) <= limit
            chunk = response.json()
            result.append(chunk['body_json_chunk'])
            if chunk['complete']:
                break
            assert chunk['next_offset'] > offset
            offset = chunk['next_offset']
        assert json.loads(''.join(result))['data']['content'] == text
        gap = await client.get(f'/api/v1/scopes/chats/runs/{rid}/events?after=0')
        assert gap.status_code == 409 and gap.json()['error']['code'] == 'resync_required'
        assert (await client.get(f'/api/v1/scopes/chats/runs/{rid}/events?after={job.last_sequence}')).status_code == 200
        assert all(len(json.dumps(event, ensure_ascii=False).encode()) <= limit for event in job.events)
        assert (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('unused'),
            headers={'Authorization': ''})).status_code == 401
        assert len(model.seen) == 1


@async_test
async def test_completed_archive_corrects_stale_live_draft_without_reexecuting(monkeypatch, tmp_path):
    model = StreamingToolModel(replies=[AIMessage(id='answer', content='saved final answer')])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        rid = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('question'))).json()['run_id']
        await web.state.delivery.wait(rid)
        job = web.state.delivery.get(rid)
        job.projection.seed(sid, [{'id': 'answer', 'type': 'ai', 'content': 'saved',
            'provisional': True, 'state': 'unconfirmed', 'run_id': rid}])
        job.projection.incomplete = True  # A genuine optional observation failure stays diagnostic.
        view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
        row = view['messages'][-1]
        assert row['content'] == 'saved final answer'
        assert row['provisional'] is False and row['state'] == 'complete'
        assert row['run_id'] == rid and row['detail']
        assert view['live']['incomplete'] is True
        assert len(model.seen) == 1


@async_test
async def test_active_revision_keeps_live_draft_instead_of_older_archive(monkeypatch, tmp_path):
    model = StreamingToolModel(replies=[AIMessage(id='same-id', content='old saved answer'),
        AIMessage(id='same-id', content='new streaming answer')])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        first = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('first'))).json()['run_id']
        await web.state.delivery.wait(first)
        entered, release = asyncio.Event(), asyncio.Event()
        model.token_entered, model.token_release = entered, release
        second = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('second'))).json()['run_id']
        try:
            await asyncio.wait_for(entered.wait(), 5)
            view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
            row = next(row for row in view['messages'] if row['id'] == 'same-id')
            assert row['content'].startswith('new stream') and row['provisional'] is True
            assert row['run_id'] == second and not view['live']['done']
        finally:
            release.set()
            await web.state.delivery.wait(second)
        assert len(model.seen) == 2


@async_test
async def test_run_finishing_during_history_read_cannot_promote_the_older_archive(monkeypatch, tmp_path):
    model = StreamingToolModel(replies=[AIMessage(id='same-id', content='old saved answer'),
        AIMessage(id='same-id', content='new streaming answer')])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        first = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('first'))).json()['run_id']
        await web.state.delivery.wait(first)
        entered, release = asyncio.Event(), asyncio.Event()
        model.token_entered, model.token_release = entered, release
        second = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('second'))).json()['run_id']
        await asyncio.wait_for(entered.wait(), 5)
        original = web.state.service.messages

        async def old_page_then_finish(identity, **query):
            page = await original(identity, **query)
            release.set()
            await web.state.delivery.wait(second)
            return page

        monkeypatch.setattr(web.state.service, 'messages', old_page_then_finish)
        try:
            rows = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()['messages']
            row = next(row for row in rows if row['id'] == 'same-id')
            assert row['content'].startswith('new stream') and row['content'] != 'old saved answer'
        finally:
            release.set()
            await web.state.delivery.wait(second)
        rows = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()['messages']
        refreshed = next(row for row in rows if row['id'] == 'same-id')
        assert refreshed['content'] == 'new streaming answer' and refreshed['provisional'] is False
        assert len(model.seen) == 2


@async_test
async def test_failed_partial_message_is_not_promoted_to_a_completed_saved_message(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    class BrokenStream(StreamingToolModel):
        async def _astream(self, *args, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content='partial visible', id='partial'))
            raise RuntimeError('private provider failure')

    async with web_client(monkeypatch, tmp_path, BrokenStream(replies=[])) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        rid = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('question'))).json()['run_id']
        await web.state.delivery.wait(rid)
        response = await client.get(f'/api/v1/scopes/chats/sessions/{sid}')
        assert response.status_code == 200
        view = response.json()
        row = next(row for row in view['messages'] if row['id'] == 'partial')
        assert view['live']['status'] == 'failed' and view['live']['done']
        assert row['content'] == 'partial visible' and row['provisional'] is True and row['state'] == 'unconfirmed'
        assert not row.get('detail') and 'private provider failure' not in response.text


@pytest.mark.parametrize('status, settled', [('completed', False), ('failed', True), ('stopped', True),
    ('interrupted', True), ('unconfirmed', True)])
@async_test
async def test_archive_fallback_requires_a_successful_settled_scope(monkeypatch, tmp_path, status, settled):
    model = StreamingToolModel(replies=[AIMessage(id='same-id', content='old saved answer')])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        rid = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('question'))).json()['run_id']
        await web.state.delivery.wait(rid)
        job = web.state.delivery.get(rid)
        job.status, job.done = status, settled
        job.projection.seed(sid, [{'id': 'same-id', 'type': 'ai', 'content': 'new partial',
            'provisional': True, 'state': 'unconfirmed', 'run_id': rid}])
        response = await client.get(f'/api/v1/scopes/chats/sessions/{sid}')
        row = response.json()['messages'][-1]
        assert row['content'] == 'new partial' and row['provisional'] is True and row['state'] == 'unconfirmed'
        assert response.json()['live']['status'] == status and response.json()['live']['done'] is settled
        job.done = True
        assert len(model.seen) == 1


@async_test
async def test_stopped_stream_keeps_partial_output_unconfirmed(monkeypatch, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    model = StreamingToolModel(replies=[AIMessage(id='partial', content='received partial response')],
        token_entered=entered, token_release=release)
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        rid = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('question'))).json()['run_id']
        try:
            await asyncio.wait_for(entered.wait(), 5)
            stopped = await client.post(f'/api/v1/scopes/chats/runs/{rid}/controls', json={
                'request_id': request('')['request_id'], 'kind': 'stop'})
            assert stopped.status_code == 202
            await asyncio.wait_for(web.state.delivery.wait(rid), 5)
            view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
            row = next(row for row in view['messages'] if row['id'] == 'partial')
            assert view['live']['status'] == 'stopped' and view['live']['done']
            assert row['content'].startswith('received') and row['provisional'] is True and row['state'] == 'unconfirmed'
        finally:
            release.set()


@async_test
async def test_oversized_child_reply_is_complete_while_parent_runs_and_after_archive_sync(monkeypatch, tmp_path):
    from naiyue_test_agent.settings import AppSettings
    from test_async_application import call
    from test_subagent_execution import ChildModel, role

    text = 'child-only 中文😀\n' * 60000
    entered, release = asyncio.Event(), asyncio.Event()
    model = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'inspect'}, 'outer'),
        AIMessage(id='large-child', content=text, response_metadata={'finish_reason': 'stop'}),
        AIMessage(content='root done')])

    async def wait_at_parent(messages):
        if len(model.seen) == 2:
            entered.set()
            await release.wait()

    model.on_request = wait_at_parent
    settings = AppSettings(subagents=(role(tool_names=('read',), model_id=None),))
    async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path, runtime_settings=settings) as (client, web):
        sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
        rid = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('inspect'))).json()['run_id']
        try:
            await asyncio.wait_for(entered.wait(), 10)
            record, = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/subagents')).json()
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}/subagents/{record["agent_id"]}'
            response = await client.get(endpoint)
            assert response.status_code == 200 and len(response.content) <= web.state.delivery.settings.max_frame_bytes
            row = response.json()['messages'][-1]
            assert row['id'] == 'large-child' and row['provisional'] is False and row['content_truncated']
            assert not web.state.delivery.get(rid).done
            root = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
            assert all(item['id'] != 'large-child' for item in root['messages'])
        finally:
            release.set()
            await web.state.delivery.wait(rid)
        job = web.state.delivery.get(rid)
        job.agents[record['agent_path']]['messages'][-1].update(
            content='stale child draft', provisional=True, state='unconfirmed')
        row = (await client.get(endpoint)).json()['messages'][-1]
        assert row['id'] == 'large-child' and row['provisional'] is False and row['detail']
        assert row['content'].startswith('child-only') and row['content_truncated']
        pieces, offset = [], 0
        while True:
            detail = await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages/{row["sequence"]}/content', params={
                'agent_id': record['agent_id'], 'revision': row['revision'], 'offset': offset})
            assert detail.status_code == 200
            data = detail.json()
            pieces.append(data['body_json_chunk'])
            if data['complete']:
                break
            assert data['next_offset'] > offset
            offset = data['next_offset']
        assert json.loads(''.join(pieces))['data']['content'] == text
        assert len(model.seen) == 3
