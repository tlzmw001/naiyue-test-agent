"""New user runs deliver status through the real prompt and response gates."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from test_async_application import StreamingToolModel, call
from test_background_tools import background_app, command, tool_result
from test_background_tasks import wait_status
from test_background_notices import completed


def system_text(messages):
    return '\n'.join(m.content for m in messages if isinstance(m, SystemMessage))


@pytest.mark.parametrize('persistent', [False, True])
def test_real_background_completion_waits_for_new_user_and_is_not_saved_as_chat(monkeypatch, tmp_path, persistent):
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': command("print('PRIVATE_STDOUT')"),
            'background': True}), AIMessage(content='started'), AIMessage(content='checked'), AIMessage(content='later')])
        async with background_app(monkeypatch, tmp_path, model, persistent=persistent) as app:
            sid = await app.acreate_workspace_session('A', tmp_path)
            first = await app.arun(sid, [('user', 'start')])
            identity = json.loads(tool_result(first, 'bash').content)['task_id']
            manager = app._sessions.background_tasks
            await wait_status(manager, identity, lambda r: r['status'] == 'finished')
            assert len(model.seen) == 2
            assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
            result = await app.arun(sid, [('user', 'check')])
            text = system_text(model.seen[2])
            assert identity in text and 'Background Shell task updates' in text
            assert 'PRIVATE_STDOUT' not in text
            assert all('Background Shell task updates' not in str(m.content) for m in result['messages'])
            assert not manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
            await app.arun(sid, [('user', 'next')])
            assert 'Background Shell task updates' not in system_text(model.seen[3])
    asyncio.run(scenario())


def test_configured_batch_preview_budget_and_later_remainder(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='one'), AIMessage(content='two')])
        async with background_app(monkeypatch, tmp_path, model, notice_items=1, notice_bytes=1024) as app:
            sid = await app.acreate_workspace_session('A', tmp_path)
            manager = app._sessions.background_tasks
            first, second = await completed(manager, tmp_path, sid=sid, count=2)
            before = manager.notice_snapshot(session_id=sid, workspace_root=tmp_path)
            preview = await app.aget_context_view(sid)
            again = await app.aget_context_view(sid)
            assert preview['current'] == again['current'] and model.seen == []
            system = next(g for g in preview['current']['estimate']['groups'] if g['id'] == 'system')
            assert next(g for g in system['children'] if g['id'] == 'background_tasks')['count'] == 1
            assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path) == before
            await app.arun(sid, [('user', 'one')])
            assert first in system_text(model.seen[0]) and second not in system_text(model.seen[0])
            latest = (await app.aget_context_view(sid))['latest_request']
            children = next(g for g in latest['estimate']['groups'] if g['id'] == 'system')['children']
            assert any(g['id'] == 'background_tasks' and g['tokens'] > 0 for g in children)
            await app.arun(sid, [('user', 'two')])
            assert second in system_text(model.seen[1]) and first not in system_text(model.seen[1])
            assert not manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
    asyncio.run(scenario())


@pytest.mark.parametrize('reply', [RuntimeError('PRIVATE_MODEL_ERROR'),
    AIMessage(content='invalid', response_metadata={'finish_reason': 'content_filter'})])
def test_failed_or_rejected_response_keeps_notice_for_next_user(monkeypatch, tmp_path, reply):
    async def scenario():
        model = StreamingToolModel(replies=[reply, AIMessage(content='retry')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.acreate_workspace_session('A', tmp_path)
            manager = app._sessions.background_tasks
            identity, = await completed(manager, tmp_path, sid=sid)
            from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
            expected = RuntimeError if isinstance(reply, RuntimeError) else ModelResponseRejected
            with pytest.raises(expected) as caught:
                await app.arun(sid, [('user', 'first')])
            if isinstance(reply, RuntimeError):
                assert caught.value is reply
            else:
                assert caught.value.reason == 'stop_reason_error'
            assert identity in system_text(model.seen[0])
            assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
            await app.arun(sid, [('user', 'retry')])
            assert identity in system_text(model.seen[1])
            assert not manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
    asyncio.run(scenario())


def test_ack_failure_is_diagnosed_without_replacing_success(monkeypatch, tmp_path, caplog):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='success')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.acreate_workspace_session('A', tmp_path)
            manager = app._sessions.background_tasks
            await completed(manager, tmp_path, sid=sid)
            def fail(*args, **kwargs):
                raise OSError('PRIVATE_ACK_ERROR')
            monkeypatch.setattr(manager, 'acknowledge_notices', fail)
            result = await app.arun(sid, [('user', 'run')])
            assert result['messages'][-1].content == 'success'
            assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
            assert 'background_notice_ack_failed' in caplog.text and 'PRIVATE_ACK_ERROR' not in caplog.text
    asyncio.run(scenario())


def test_user_stop_keeps_notice_and_does_not_start_another_model(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='never accepted')], entered=entered, release=release)
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.acreate_workspace_session('A', tmp_path)
            manager = app._sessions.background_tasks
            identity, = await completed(manager, tmp_path, sid=sid)
            pending = asyncio.create_task(app.arun(sid, [('user', 'run')]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                app.request_stop(sid)
                release.set()
                result = await pending
                assert result['run_stopped'] and len(model.seen) == 1
                assert identity in system_text(model.seen[0])
                assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(scenario())


def test_terminal_after_frozen_snapshot_waits_for_later_user(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='first'), AIMessage(content='next')],
            entered=entered, release=release)
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.acreate_workspace_session('A', tmp_path)
            pending = asyncio.create_task(app.arun(sid, [('user', 'first')]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                manager = app._sessions.background_tasks
                identity, = await completed(manager, tmp_path, sid=sid)
                release.set()
                await pending
                assert identity not in system_text(model.seen[0])
                assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
                await app.arun(sid, [('user', 'next')])
                assert identity in system_text(model.seen[1])
                assert not manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(scenario())


def test_budget_too_small_and_non_user_input_do_not_consume_notice(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='done')])
        async with background_app(monkeypatch, tmp_path, model, notice_bytes=1) as app:
            sid = await app.acreate_workspace_session('A', tmp_path)
            manager = app._sessions.background_tasks
            identity, = await completed(manager, tmp_path, sid=sid)
            await app.arun(sid, [('user', 'run')])
            assert identity not in system_text(model.seen[0])
            assert manager._pending_notices[sid]
        model = StreamingToolModel(replies=[AIMessage(content='done')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.acreate_workspace_session('A', tmp_path)
            manager = app._sessions.background_tasks
            identity, = await completed(manager, tmp_path, sid=sid)
            await app.arun(sid, [AIMessage(content='internal input')])
            assert identity not in system_text(model.seen[0])
            assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
    asyncio.run(scenario())


def test_approval_resume_does_not_collect_new_notices(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': 'printf approved'}),
            AIMessage(content='approved'), AIMessage(content='new user')])
        async with background_app(monkeypatch, tmp_path, model, mode='Default') as app:
            sid = await app.acreate_workspace_session('A', tmp_path)
            await app.arun(sid, [('user', 'command')])
            manager = app._sessions.background_tasks
            identity, = await completed(manager, tmp_path, sid=sid)
            result = await app.aresume_tool_approval(sid, ['approve'])
            assert result['messages'][-1].content == 'approved'
            assert identity not in system_text(model.seen[1])
            assert manager.notice_snapshot(session_id=sid, workspace_root=tmp_path).versions
            await app.arun(sid, [('user', 'next')])
            assert identity in system_text(model.seen[2])
    asyncio.run(scenario())
