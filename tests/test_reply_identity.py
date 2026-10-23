"""Reply ownership comes from graph facts, including across approval runs."""

import json
from langchain_core.messages import AIMessage, HumanMessage

from native_application_helpers import async_test
from test_persistent_sessions import build
from test_application_run_events import ToolModel
from naiyue_test_agent.tools import create_session_read_tool


@async_test
async def test_all_tool_rounds_share_task_and_next_task_is_distinct(monkeypatch, tmp_path):
    (tmp_path / 'note').write_text('body')
    model = ToolModel(responses=[AIMessage(content='reading', tool_calls=[
        {'name': 'read', 'id': 'call', 'args': {'path': 'note'}}]),
        AIMessage(content='done'), AIMessage(content='next')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model, [create_session_read_tool()]) as app:
        sid = await app.acreate_workspace_session('A', tmp_path)
        events = []
        app.subscribe_events(events.append)
        result = await app.arun(sid, [HumanMessage(content='read')])
        reply = result['messages'][0].additional_kwargs['naiyue_reply_id']
        assert all(m.additional_kwargs.get('naiyue_reply_id') == reply for m in result['messages'] if m.type != 'tool')
        rows = app._storage.conversation.read_chat_page(sid, app._storage.sessions.get(sid)['thread_id'], limit=20, body_bytes=10000, preview_chars=100)
        assert {r['reply_id'] for r in rows['records']} == {reply}
        snapshots = [json.loads(e.data.snapshot_json) for e in events if e.event_type == 'message_completed']
        assert snapshots and all(row['reply_id'] == reply for row in snapshots)
        result = await app.arun(sid, [('user', 'next')])
        assert result['messages'][-1].additional_kwargs['naiyue_reply_id'] != reply


@async_test
async def test_approval_resume_keeps_original_reply(monkeypatch, tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('allowed')
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'id': 'call', 'args': {'path': str(outside)}}]), AIMessage(content='done')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model, [create_session_read_tool()]) as app:
        sid = await app.acreate_workspace_session('A', root)
        paused = await app.arun(sid, [('user', 'read outside')])
        reply = paused['messages'][0].additional_kwargs['naiyue_reply_id']
        resumed = await app.aresume_tool_approval(sid, ['approve'])
        assert resumed['messages'][-1].additional_kwargs['naiyue_reply_id'] == reply
