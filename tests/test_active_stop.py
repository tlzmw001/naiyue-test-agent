"""User stop is run-scoped; cancellation must leave a safe protocol boundary."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import asyncio
from threading import Event

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from test_async_application import StreamingToolModel, make_app, call


def test_stop_cancels_model_without_fake_answer_and_can_start_again(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='next')],
                                   entered=entered, release=release)
        app = await make_app(monkeypatch, model)
        session = await app.acreate_session('A')
        events = []
        app.subscribe_events(events.append)
        task = asyncio.create_task(app.arun(session, [('user', 'start')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            app.steer(session, 'keep steering')
            app.follow_up(session, 'keep follow-up')
            run_id = events[0].run_id
            with pytest.raises(application.SessionBusyError):
                app.request_stop(session, run_id='stale')
            retained = app.request_stop(session, run_id=run_id)
            assert retained == ['keep steering', 'keep follow-up']
            assert app.request_stop(session, run_id=run_id) == []
            result = await asyncio.wait_for(task, 5)
            assert result['run_stopped'] is True
            assert [m.type for m in result['messages']] == ['human']
            snapshot = await app._agent.aget_state({'configurable': {
                'thread_id': app._sessions._sessions[session]['thread_id']}})
            assert not snapshot.next and not any(t.error for t in snapshot.tasks)
            assert events[-1].data.status == 'stopped' and events[-1].data.stop_mode == 'active'
            assert events[-1].data.manual_review_required is False
            assert len([e for e in events if e.event_type == 'model_call_finished']) == 1
            assert not any(e.event_type == 'message_completed' and e.data.message_type == 'ai' for e in events)
            release.set()
            following = await app.arun(session, [('user', 'new')])
            assert not following['run_stopped']
            assert following['messages'][-1].content == 'next'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_summary_wait_stops_without_compaction_fallback(monkeypatch, caplog):
    from test_async_context_chain import model, history

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        chosen = model(summary_entered=entered, summary_release=release)
        app = await make_app(monkeypatch, chosen)
        session = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(session, history()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            app.request_stop(session)
            result = await asyncio.wait_for(task, 5)
            assert result['run_stopped'] and chosen.normal_inputs == []
            assert [m.id for m in result['messages']] == [m.id for m in history()]
            assert not any(getattr(r, 'context_compaction', {}).get('event') == 'context_compaction_fallback'
                           for r in caplog.records)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_stopped_checkpoint_is_ready_and_restores(monkeypatch, tmp_path):
    from test_async_persistent import factory

    async def scenario():
        path = tmp_path / 'sessions.sqlite'
        entered, release = asyncio.Event(), asyncio.Event()
        chosen = StreamingToolModel(replies=[], entered=entered, release=release)
        async with factory(monkeypatch, path, chosen) as app:
            session = await app.acreate_session('A')
            task = asyncio.create_task(app.arun(session, [('user', 'stopped request')]))
            await asyncio.wait_for(entered.wait(), 5)
            app.request_stop(session)
            result = await asyncio.wait_for(task, 5)
            assert result['run_stopped']
            row = (await app.alist_sessions())[0]
            assert row['state'] == 'ready' and row['last_run_status'] == 'stopped'
        async with factory(monkeypatch, path, StreamingToolModel(replies=[AIMessage(content='restored')])) as app:
            assert await app.arestore_session(session) == session
            result = await app.arun(session, [('user', 'continue')])
            assert [m.content for m in result['messages']] == ['stopped request', 'continue', 'restored']
    asyncio.run(scenario())


@pytest.mark.parametrize('sequential', [False, True])
def test_stop_drains_started_tool_but_skips_waiting_tool(monkeypatch, sequential):
    async def scenario():
        entered, release, second = Event(), Event(), Event()

        @tool
        def writing() -> str:
            """Synthetic mutation that must settle before the run releases busy."""
            entered.set()
            assert release.wait(5)
            return 'committed'

        @tool
        def later() -> str:
            """Must not be started after stop."""
            second.set()
            return 'unexpected'

        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'writing', 'args': {}, 'id': 'one'},
            {'name': 'later', 'args': {}, 'id': 'two'},
        ])])
        app = await make_app(monkeypatch, model, [ToolSpec(writing, writing.args_schema, execution_mode='sequential' if sequential else 'parallel', permissions=ToolPermission(default='allow')), ToolSpec(later, later.args_schema, permissions=ToolPermission(default='allow'))])
        session = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(session, [('user', 'start')], max_concurrency=1))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            app.request_stop(session)
            assert app._sessions._sessions[session]['busy']
            with pytest.raises(application.SessionBusyError):
                await app.arun(session, [('user', 'cannot overlap')])
            release.set()
            result = await asyncio.wait_for(task, 5)
            results = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
            assert results['one'].content == 'committed'
            assert results['two'].status == 'error' and 'user' in results['two'].content.lower()
            assert not second.is_set() and result['run_stopped']
            assert len(model.seen) == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_stop_before_model_sends_no_request(monkeypatch):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='must not run')])
        app = await make_app(monkeypatch, model)
        session = await app.acreate_session('A')

        def stop_at_start(event):
            if event.event_type == 'run_started':
                app.request_stop(session, run_id=event.run_id)

        app.subscribe_events(stop_at_start)
        try:
            result = await app.arun(session, [('user', 'start')])
            assert result['run_stopped'] and model.seen == []
            assert [m.type for m in result['messages']] == ['human']
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_external_cancel_remains_failed_and_is_not_user_stop(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='unused')], entered=entered, release=release)
        app = await make_app(monkeypatch, model)
        session = await app.acreate_session('A')
        events = []
        app.subscribe_events(events.append)
        task = asyncio.create_task(app.arun(session, [('user', 'start')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert events[-1].data.status == 'failed'
            assert events[-1].data.failure.reason == 'task_cancelled'
            assert events[-1].data.manual_review_required
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_stop_during_text_stream_keeps_draft_unconfirmed(monkeypatch):
    from naiyue_test_agent.application.stream_projection import StreamingMessageProjection

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        chosen = StreamingToolModel(replies=[AIMessage(content='partial answer')],
                                    token_entered=entered, token_release=release)
        app = await make_app(monkeypatch, chosen)
        session = await app.acreate_session('A')
        projection = StreamingMessageProjection()
        delta_seen = asyncio.Event()

        def observe(event):
            projection(event)
            if event.event_type == 'message_delta':
                delta_seen.set()

        app.subscribe_events(observe)
        task = asyncio.create_task(app.arun(session, [('user', 'start')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(delta_seen.wait(), 5)
            app.request_stop(session)
            result = await asyncio.wait_for(task, 5)
            assert [m.type for m in result['messages']] == ['human']
            draft = next(row for row in projection.messages if row['type'] == 'ai')
            assert draft['content'] == 'partial' and draft['state'] == 'unconfirmed'
            assert draft['provisional'] is True
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_stop_during_real_write_waits_for_atomic_replace(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import create_session_write_tool
    from naiyue_test_agent.tools import write as write_module

    async def scenario():
        entered, release = Event(), Event()
        target = tmp_path / 'result.txt'
        target.write_text('old')
        original = write_module.os.replace

        def replace(source, destination, *args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(source, destination, *args, **kwargs)

        monkeypatch.setattr(write_module.os, 'replace', replace)
        chosen = StreamingToolModel(replies=[call('write', {'path': 'result.txt', 'content': 'new'})])
        app = await make_app(monkeypatch, chosen, [create_session_write_tool()])
        session = await app.acreate_workspace_session('A', tmp_path)
        task = asyncio.create_task(app.arun(session, [('user', 'write')]))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            app.request_stop(session)
            assert target.read_text() == 'old' and app._sessions._sessions[session]['busy']
            release.set()
            result = await asyncio.wait_for(task, 5)
            assert target.read_text() == 'new' and result['run_stopped']
            message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert message.status == 'success'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
