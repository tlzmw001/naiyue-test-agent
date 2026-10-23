"""The archive owns display history; checkpoint messages remain execution context."""

import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent.application.errors import SessionPersistenceError
from test_persistent_sessions import build
from test_application_run_events import ToolModel


def rows(storage, sid):
    return [dict(row) for row in storage.database.connection.execute(
        'SELECT * FROM naiyue_chat_messages WHERE session_id=? ORDER BY thread_id,sequence', (sid,))]


@async_test
async def test_finish_archives_full_messages_and_same_content_distinct_ids(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import create_session_read_tool
    (tmp_path / 'note').write_text('saved tool content')
    model = ToolModel(responses=[AIMessage(id='call', content='', tool_calls=[
        {'id': 'c', 'name': 'read', 'args': {'path': 'note'}}]), AIMessage(id='reply', content='same'),
        AIMessage(id='reply2', content='same')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model, [create_session_read_tool()]) as app:
        sid = await app.sessions.create_workspace('A', tmp_path)
        await app.runs.start(sid, [HumanMessage(id='h1', content='same')])
        await app.runs.start(sid, [HumanMessage(id='h2', content='same')])
        storage = app.sessions._persistence.storage
        messages = await app.history.messages(sid)
        archived = rows(storage, sid)
        assert [r['message_id'] for r in archived] == [m.id for m in messages]
        assert [r['sequence'] for r in archived] == list(range(1, 7))
        assert all(r['revision'] == 1 for r in archived)
        assert json.loads(archived[2]['body_json'])['data']['tool_call_id'] == 'c'
        assert json.loads(archived[2]['body_json'])['data']['content'] == 'saved tool content'
        head, = storage.conversation.chat_heads(sid)
        assert head['checkpoint_id'] == storage.sessions.get(sid)['checkpoint_id']


@async_test
async def test_repeated_sync_revises_in_place_and_reads_no_history_at_same_boundary(monkeypatch, tmp_path):
    from naiyue_test_agent.application.conversation.chat_history import collect_chat_updates
    async with build(monkeypatch, tmp_path / 'state.sqlite', ToolModel(responses=[AIMessage(id='ai', content='first')])) as app:
        sid = await app.sessions.create('A')
        await app.runs.start(sid, [HumanMessage(id='h', content='question')])
        storage = app.sessions._persistence.storage
        before = rows(storage, sid)
        config = {'configurable': {'thread_id': storage.sessions.get(sid)['thread_id']}}
        snapshot = await app.runs._agent.aget_state(config)
        async def forbidden(*args, **kwargs):
            raise AssertionError('Already archived checkpoint needs no history scan')
            yield
        with monkeypatch.context() as patch:
            patch.setattr(app.runs._agent, 'aget_state_history', forbidden)
            updates = await collect_chat_updates(app.runs._agent, storage, sid)
        storage.runs.finish_run(sid, 'ready', snapshot.config['configurable']['checkpoint_id'], '', 'completed', updates)
        assert rows(storage, sid) == before
        await app.runs._agent.aupdate_state(snapshot.config, {'messages': [AIMessage(id='ai', content='corrected',
            usage_metadata={'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5})]})
        updates = await collect_chat_updates(app.runs._agent, storage, sid)
        latest = await app.runs._agent.aget_state(config)
        storage.runs.finish_run(sid, 'ready', latest.config['configurable']['checkpoint_id'], '', 'completed', updates)
        after = rows(storage, sid)
        assert [r['message_id'] for r in after] == ['h', 'ai']
        assert after[0] == before[0] and after[1]['sequence'] == before[1]['sequence']
        assert after[1]['revision'] == 2
        assert json.loads(after[1]['body_json'])['data']['usage_metadata']['total_tokens'] == 5
        with pytest.raises(SessionPersistenceError, match='chat_history_stale'):
            storage.runs.finish_run(sid, 'ready', 'stale', '', 'completed', updates)
        assert rows(storage, sid) == after and storage.sessions.get(sid)['checkpoint_id'] != 'stale'


@async_test
async def test_manual_compaction_keeps_originals_after_reopen(monkeypatch, tmp_path):
    from test_manual_compaction import compact_model, history
    from test_web_api import web_client
    model = compact_model()
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = await history(client)
        app = web.state.service.application
        storage = app.sessions._persistence.storage
        before = rows(storage, sid)
        response = await client.post(f'/api/sessions/{sid}/compact', json={'request_id': str(uuid4())})
        await web.state.delivery.wait(response.json()['run_id'])
        context = await app.history.messages(sid)
        assert not any(m.id == before[0]['message_id'] for m in context)
        after = rows(storage, sid)
        assert after[:len(before)] == before
        assert after[-1]['kind'] == 'context_summary'
        path = storage.path
    async with build(monkeypatch, path) as app:
        storage = app.sessions._persistence.storage
        assert rows(storage, sid) == after
        await app.sessions.restore(sid)
        assert [m.id for m in await app.history.messages(sid)] == [m.id for m in context]


@async_test
async def test_archive_failure_rolls_back_messages_cursor_and_metadata(monkeypatch, tmp_path, caplog):
    async with build(monkeypatch, tmp_path / 'state.sqlite', ToolModel(responses=[AIMessage(content='answer')])) as app:
        sid = await app.sessions.create('A')
        storage = app.sessions._persistence.storage
        storage.database.connection.execute('''CREATE TRIGGER fail_archive BEFORE INSERT ON naiyue_chat_sync
            BEGIN SELECT RAISE(ABORT, 'PRIVATE_ARCHIVE'); END''')
        storage.database.connection.commit()
        events = []
        app.observability.subscribe(events.append)
        with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
            await app.runs.start(sid, [('user', 'q')])
        assert rows(storage, sid) == [] and storage.conversation.chat_heads(sid) == []
        assert storage.sessions.get(sid)['state'] == 'running'
        assert events[-1].data.status == 'failed'
        with pytest.raises(SessionPersistenceError):
            await app.runs.start(sid, [('user', 'do not replay')])
        assert 'PRIVATE_ARCHIVE' not in caplog.text


@async_test
async def test_archive_failure_preserves_primary_exception(monkeypatch, tmp_path, caplog):
    primary = RuntimeError('PRIVATE_PRIMARY')
    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise primary
    async with build(monkeypatch, tmp_path / 'state.sqlite', Failing(responses=[])) as app:
        sid = await app.sessions.create('A')
        storage = app.sessions._persistence.storage
        storage.database.connection.execute('''CREATE TRIGGER fail_archive BEFORE INSERT ON naiyue_chat_messages
            BEGIN SELECT RAISE(ABORT, 'PRIVATE_ARCHIVE'); END''')
        storage.database.connection.commit()
        with pytest.raises(RuntimeError) as caught:
            await app.runs.start(sid, [('user', 'q')])
        assert caught.value is primary and rows(storage, sid) == []
        assert storage.sessions.get(sid)['state'] == 'running'
        assert 'session_persistence_secondary_failed' in caplog.text and 'PRIVATE_' not in caplog.text


@async_test
async def test_approval_pause_archives_unpaired_call_then_resume_pairs_once(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import create_session_read_tool
    root = tmp_path / 'work'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('approved content')
    model = ToolModel(responses=[AIMessage(id='ai', content='', tool_calls=[
        {'id': 'c', 'name': 'read', 'args': {'path': str(outside)}}]), AIMessage(id='done', content='done')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model, [create_session_read_tool()]) as app:
        sid = await app.sessions.create_workspace('A', root)
        storage = app.sessions._persistence.storage
        paused = await app.runs.start(sid, [HumanMessage(id='h', content='read outside')])
        assert paused['approval_requests'] and [r['message_id'] for r in rows(storage, sid)] == ['h', 'ai']
        assert storage.sessions.get(sid)['state'] == 'interrupted'
        await app.runs.resume_approval(sid, ['approve'])
        archived = rows(storage, sid)
        assert len(archived) == 4 and [r['sequence'] for r in archived] == [1, 2, 3, 4]
        assert all(r['revision'] == 1 for r in archived)
        assert json.loads(archived[2]['body_json'])['data']['tool_call_id'] == 'c'
        assert json.loads(archived[2]['body_json'])['data']['content'] == 'approved content'
        assert storage.sessions.get(sid)['state'] == 'ready'
