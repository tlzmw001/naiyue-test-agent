"""Archive cursors bind ownership and revisions, independently of appending context."""

from legacy_store_schema import remove_reply_schema

import asyncio
import json
import sqlite3

import pytest
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel, call
from test_web_api import web_client, request
from native_application_helpers import await_diagnostics_idle


async def run(client, web, sid, text='request'):
    response = await client.post(f'/api/sessions/{sid}/runs', json=request(text))
    assert response.status_code == 202
    await web.state.delivery.wait(response.json()['run_id'])


def test_cursor_is_scope_bound_append_stable_and_revision_sensitive(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(id=f'a{i}', content=f'answer {i}') for i in range(3)])
        async with web_client(monkeypatch, tmp_path, model, history_page_messages=2) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            await run(client, web, sid, 'one')
            await run(client, web, sid, 'two')
            first = (await client.get(f'/api/sessions/{sid}/messages')).json()
            token = first['next_cursor']
            await run(client, web, sid, 'three')
            older = await client.get(f'/api/sessions/{sid}/messages', params={'before': token})
            assert [r['content'] for r in older.json()['messages']] == ['one', 'answer 0']
            assert older.json()['coverage']['upper'] == first['coverage']['upper'] == 4
            other = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            for target, cursor in ((other, token), (sid, token + 'x'), (sid, 'not_a_cursor')):
                response = await client.get(f'/api/sessions/{target}/messages', params={'before': cursor})
                assert response.status_code == 422 and response.json()['error']['code'] == 'invalid_history_cursor'
            assert (await client.get(f'/api/sessions/{sid}/messages', params={'agent_id': 'unknown'})).status_code == 404
            app = web.state.service.application
            store = app._storage
            config = {'configurable': {'thread_id': store.sessions.get(sid)['thread_id']}}
            await app._agent.aupdate_state(config, {'messages': [AIMessage(id='a0', content='corrected')]})
            assert (await client.post(f'/api/sessions/{sid}/prepare')).status_code == 200
            stale = await client.get(f'/api/sessions/{sid}/messages', params={'before': token})
            assert stale.status_code == 409 and stale.json()['error']['code'] == 'history_cursor_stale'
            assert (await client.get(f'/api/sessions/{sid}/messages/2/content', params={'revision': 1})).status_code == 409
            assert (await client.get(f'/api/sessions/{sid}/messages/2/content', params={'revision': 2, 'offset': -1})).status_code == 422
            assert (await client.get(f'/api/sessions/{sid}/messages/999/content', params={'revision': 1})).status_code == 404
            assert len(model.seen) == 3
    asyncio.run(scenario())


def test_single_message_pages_keep_tool_ownership_when_call_ids_repeat(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'work'
        root.mkdir()
        (root / 'note').write_text('file result')
        responses = []
        for index in range(2):
            response = call('read', {'path': 'note'}, 'reused')
            response.id = f'call-{index}'
            responses.extend([response, AIMessage(id=f'answer-{index}', content='done')])
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=responses),
                              workspace_root=root, history_page_messages=1) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            await run(client, web, sid)
            await run(client, web, sid)
            cursor, received = None, []
            for _ in range(10):
                page = (await client.get(f'/api/sessions/{sid}/messages', params={'before': cursor} if cursor else {})).json()
                received = page['messages'] + received
                if not page['has_more']: break
                assert page['next_cursor'] and page['next_cursor'] != cursor
                cursor = page['next_cursor']
            assert len(received) == 8 and len({r['id'] for r in received}) == 8
            results = [r for r in received if r['type'] == 'tool']
            assert [r['parent_message_id'] for r in results] == ['call-0', 'call-1']
            assert [r['sequence'] for r in received] == list(range(1, 9))
    asyncio.run(scenario())


def test_gets_use_archive_sql_without_graph_or_complete_large_bodies(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='x' * 100000)])) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            await run(client, web, sid)
            app = web.state.service.application
            store = app._storage
            await await_diagnostics_idle(app)
            before = store.database.connection.total_changes
            async def forbidden(*args, **kwargs):
                raise AssertionError('Display must not fetch execution state')
            monkeypatch.setattr(app._agent, 'aget_state', forbidden)
            monkeypatch.setattr(app, 'aget_messages', forbidden)
            queries = []
            store.database.connection.set_trace_callback(queries.append)
            try:
                assert (await client.get(f'/api/sessions/{sid}')).status_code == 200
                page = (await client.get(f'/api/sessions/{sid}/messages')).json()
                raw = store.conversation.read_chat_page(sid, store.sessions.get(sid)['thread_id'], limit=1, body_bytes=16,
                                          preview_chars=32)['records'][0]
                assert raw['body_json'] is None and len(raw['preview']) == 32
                assert page['messages'][-1]['content_truncated']
                assert store.database.connection.total_changes == before
                assert not any('FROM checkpoints' in q or 'FROM writes' in q for q in queries)
                assert any('LIMIT' in q and 'CASE WHEN length' in q for q in queries)
            finally:
                store.database.connection.set_trace_callback(None)
    asyncio.run(scenario())


def test_schema_seven_migration_recovers_types_and_tool_parent_ids(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'work'
        root.mkdir()
        (root / 'note').write_text('saved')
        first = call('read', {'path': 'note'}, 'call')
        first.id = 'owner'
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[first, AIMessage(content='done')]), workspace_root=root) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            await run(client, web, sid)
        with sqlite3.connect(tmp_path / 'sessions.sqlite') as connection:
            remove_reply_schema(connection)
            connection.executescript('''DROP TABLE naiyue_model_invocations; DROP TABLE naiyue_model_usage_runs;
            ALTER TABLE naiyue_chat_sync DROP COLUMN history_version;
                ALTER TABLE naiyue_chat_sync DROP COLUMN stopped;
                ALTER TABLE naiyue_chat_messages DROP COLUMN message_type;
                ALTER TABLE naiyue_chat_messages DROP COLUMN parent_message_id;
                UPDATE naiyue_store_info SET version=7;''')
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, web):
            page = (await client.get(f'/api/sessions/{sid}/messages')).json()
            result, = [r for r in page['messages'] if r['type'] == 'tool']
            assert result['parent_message_id'] == 'owner' and result['content'] == 'saved'
            assert model.seen == [] and not web.state.service.application.has_session(sid)
    asyncio.run(scenario())


def test_workspace_listing_and_admission_do_not_load_all_session_metadata(monkeypatch, tmp_path):
    async def scenario():
        root, other = tmp_path / 'work', tmp_path / 'other'
        root.mkdir()
        other.mkdir()
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), workspace_root=root) as (client, web):
            app = web.state.service.application
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            foreign = await app.acreate_workspace_session('A', other)
            def forbidden(*args, **kwargs):
                raise AssertionError('Web must not list every workspace for one session')
            monkeypatch.setattr(app._storage.sessions, 'list_sessions', forbidden)
            response = await client.get('/api/sessions')
            assert response.status_code == 200 and [r['session_id'] for r in response.json()] == [sid]
            assert (await client.get(f'/api/sessions/{sid}')).status_code == 200
            for target in (foreign, 'missing'):
                response = await client.get(f'/api/sessions/{target}')
                assert response.status_code == 404 and response.json()['error']['code'] == 'session_not_found'
    asyncio.run(scenario())


def test_large_tool_batch_marks_omitted_call_metadata(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'work'
        root.mkdir()
        (root / 'note').write_text('result')
        invocation = AIMessage(id='large-batch', content='x' * 40000, tool_calls=[
            {'id': f'call-{i}', 'name': 'read', 'args': {'path': 'note'}} for i in range(2)])
        model = StreamingToolModel(replies=[invocation, AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root, history_page_messages=1) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            await run(client, web, sid)
            cursor, batch = None, None
            for _ in range(5):
                page = (await client.get(f'/api/sessions/{sid}/messages', params={'before': cursor} if cursor else {})).json()
                batch = next((m for m in page['messages'] if m['id'] == 'large-batch'), None)
                if batch: break
                cursor = page['next_cursor']
            assert batch and batch['arguments_truncated'] and batch['tool_calls_truncated']
            assert batch['tool_calls'] == [{'id': 'call-0', 'name': 'read', 'args': {}}]
    asyncio.run(scenario())
