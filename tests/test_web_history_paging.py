"""History preparation is explicit; display limits never gate execution."""

import asyncio
import json

from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel
from test_web_api import web_client, request
from native_application_helpers import await_diagnostics_idle


async def read_pages(client, sid, *, agent_id=None):
    rows, cursor, cursors = [], None, set()
    for _ in range(256):
        query = {'agent_id': agent_id} if agent_id else {}
        if cursor:
            query['before'] = cursor
        response = await client.get(f'/api/sessions/{sid}/messages', params=query)
        assert response.status_code == 200, response.text
        page = response.json()
        rows = page['messages'] + rows
        if not page['has_more']:
            assert page['next_cursor'] is None
            assert len({row['id'] for row in rows}) == len(rows)
            assert [row['sequence'] for row in rows] == sorted(row['sequence'] for row in rows)
            return rows
        cursor = page['next_cursor']
        assert cursor and cursor not in cursors
        cursors.add(cursor)
    raise AssertionError('History did not reach its beginning')


def test_large_history_is_bounded_and_does_not_gate_a_new_run(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(id=f'a{i}', content='x' * 30000) for i in range(20)])
        async with web_client(monkeypatch, tmp_path, model, history_page_messages=3) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            for index in range(18):
                started = await client.post(f'/api/sessions/{sid}/runs', json=request('q' * 30000))
                assert started.status_code == 202
                await web.state.delivery.wait(started.json()['run_id'])
            view = await client.get(f'/api/sessions/{sid}')
            assert view.status_code == 200 and len(view.content) <= web.state.delivery.settings.max_frame_bytes
            assert not any(row.get('arguments_truncated') for row in view.json()['messages'] if row['type'] == 'ai')
            app = web.state.service.application
            original = await app.aget_messages(sid)
            assert sum(len(message.content) for message in original) > 1024 * 1024
            store = app._storage
            await await_diagnostics_idle(app)
            writes = store.connection.total_changes
            rows = await read_pages(client, sid)
            assert [row['id'] for row in rows] == [message.id for message in original]
            assert len(rows) == 36 and all(row['content_truncated'] and row['detail'] for row in rows)
            assert len(model.seen) == 18 and store.connection.total_changes == writes
            started = await client.post(f'/api/sessions/{sid}/runs', json=request('continue'))
            assert started.status_code == 202
            await web.state.delivery.wait(started.json()['run_id'])
            assert len(model.seen) == 19
    asyncio.run(scenario())


def test_prepare_loads_old_session_while_get_is_read_only(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='saved')])) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('human'))).json()['run_id']
            await web.state.delivery.wait(run)
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            app = web.state.service.application
            before = web.state.service.application._storage.connection.total_changes
            view = await client.get(f'/api/sessions/{sid}')
            assert view.status_code == 200 and not app.has_session(sid)
            assert view.json()['history_ready'] is False
            assert app._storage.connection.total_changes == before
            prepared = await client.post(f'/api/sessions/{sid}/prepare')
            assert prepared.status_code == 200 and app.has_session(sid)
            assert prepared.json()['history_ready']
            async def forbidden(*args, **kwargs):
                raise AssertionError('GET must not read execution checkpoints')
            monkeypatch.setattr(app._agent, 'aget_state', forbidden)
            ready = await client.get(f'/api/sessions/{sid}')
            assert ready.json()['can_run'] and ready.json()['messages'][-1]['content'] == 'saved'
            assert model.seen == []
    asyncio.run(scenario())


def test_pages_and_large_message_chunks_preserve_original_json(monkeypatch, tmp_path):
    async def scenario():
        text = '大段正文\\"\n' * 10000
        model = StreamingToolModel(replies=[AIMessage(id='a', content=text)])
        async with web_client(monkeypatch, tmp_path, model, history_page_messages=1) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('human'))).json()['run_id']
            await web.state.delivery.wait(run)
            page = await client.get(f'/api/sessions/{sid}/messages')
            assert page.status_code == 200
            row, = page.json()['messages']
            assert row['content_truncated'] and row['detail']['sequence'] == 2
            assert not row.get('arguments_truncated') and not row.get('tool_calls_truncated')
            chunks, offset = [], 0
            while True:
                detail = await client.get(f'/api/sessions/{sid}/messages/2/content', params={'revision': row['revision'], 'offset': offset})
                assert detail.status_code == 200
                result = detail.json()
                chunks.append(result['body_json_chunk'])
                if result['complete']: break
                assert result['next_offset'] > offset
                offset = result['next_offset']
            assert json.loads(''.join(chunks))['data']['content'] == text
            older = await client.get(f'/api/sessions/{sid}/messages', params={'before': page.json()['next_cursor']})
            assert [r['id'] for r in older.json()['messages']] == [model.seen[0][-1].id]
            assert not older.json()['has_more'] and len(model.seen) == 1
    asyncio.run(scenario())


def test_compact_and_approve_do_not_require_a_complete_display_view(monkeypatch, tmp_path):
    from test_manual_compaction import compact_model, history
    from test_async_application import call
    from test_web_tools import start, approval_request

    async def scenario():
        def forbidden(*args, **kwargs):
            raise AssertionError('Execution admission must not build display history')
        (tmp_path / 'compact').mkdir()
        (tmp_path / 'approval').mkdir()
        async with web_client(monkeypatch, tmp_path / 'compact', compact_model(), max_frame_bytes=8192) as (client, web):
            sid = await history(client)
            monkeypatch.setattr(web.state.service, 'session', forbidden)
            monkeypatch.setattr(web.state.delivery.get(web.state.delivery.latest[sid]), 'view', forbidden)
            response = await client.post(f'/api/sessions/{sid}/compact', json={'request_id': request('')['request_id']})
            assert response.status_code == 202
            rid = response.json()['run_id']
            await web.state.delivery.wait(rid)
            assert web.state.delivery.get(rid).compaction_result['status'] == 'applied'
        root = tmp_path / 'work'
        root.mkdir()
        outside = tmp_path / 'outside'
        outside.write_text('result')
        model = StreamingToolModel(replies=[call('read', {'path': str(outside)}), AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path / 'approval', model, workspace_root=root) as (client, web):
            sid, _ = await start(client)
            batch, = web.state.service.application.pending_approval_batches(sid)
            monkeypatch.setattr(web.state.service, 'session', forbidden)
            monkeypatch.setattr(web.state.delivery.get(web.state.delivery.latest[sid]), 'view', forbidden)
            response = await client.post(f'/api/sessions/{sid}/approvals', json=approval_request(batch, ['approve']))
            assert response.status_code == 202
            rid = response.json()['run_id']
            await web.state.delivery.wait(rid)
            assert web.state.delivery.get(rid).status == 'completed' and len(model.seen) == 2
    asyncio.run(scenario())
