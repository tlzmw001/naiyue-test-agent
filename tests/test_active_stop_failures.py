"""A stop request must not turn real failures into successful terminal events."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import asyncio
from threading import Event

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.application.session_storage import SessionPersistenceError
from test_async_application import make_app, StreamingToolModel, call
from test_async_persistent import factory


def test_started_tool_error_result_is_preserved_when_user_stops(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        failure = RuntimeError('PRIVATE_TOOL_ERROR')

        @tool
        async def failing() -> str:
            """A running tool must settle and preserve its actual exception."""
            entered.set()
            await release.wait()
            raise failure

        app = await make_app(monkeypatch, StreamingToolModel(replies=[call('failing', {})]), [ToolSpec(failing, failing.args_schema, permissions=ToolPermission(default='allow'))])
        session = await app.acreate_session('A')
        events = []
        app.subscribe_events(events.append)
        task = asyncio.create_task(app.arun(session, [('user', 'start')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            app.request_stop(session)
            release.set()
            result = await task
            feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert feedback.status == 'error' and feedback.name == 'failing'
            assert 'RuntimeError' in feedback.content and 'PRIVATE_' not in feedback.content
            assert result['run_stopped']
            assert events[-1].data.status == 'stopped' and not events[-1].data.manual_review_required
            end = next(e.data for e in events if e.event_type == 'tool_call_finished')
            assert end.outcome == 'error_result'
            assert len([e for e in events if e.event_type == 'model_call_started']) == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_stop_save_failure_does_not_publish_stopped(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        chosen = StreamingToolModel(replies=[], entered=entered, release=release)
        async with factory(monkeypatch, tmp_path / 'session.sqlite', chosen) as app:
            session = await app.acreate_session('A')
            events = []
            app.subscribe_events(events.append)

            def fail(*args):
                raise SessionPersistenceError('synthetic_save_failure')

            monkeypatch.setattr(app._storage, 'finish_run', fail)
            task = asyncio.create_task(app.arun(session, [('user', 'start')]))
            await asyncio.wait_for(entered.wait(), 5)
            app.request_stop(session)
            with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
                await task
            assert events[-1].data.status == 'failed' and events[-1].data.stop_mode is None
            assert (await app.alist_sessions())[0]['state'] == 'running'
    asyncio.run(scenario())


def test_other_session_keeps_running_after_first_session_stop(monkeypatch):
    async def scenario():
        entered_a, entered_b = asyncio.Event(), asyncio.Event()
        release_a, release_b = asyncio.Event(), asyncio.Event()
        a = StreamingToolModel(replies=[], entered=entered_a, release=release_a)
        b = StreamingToolModel(replies=[AIMessage(content='other session')], entered=entered_b, release=release_b)
        from naiyue_test_agent import application
        from naiyue_test_agent.model_access import ModelCatalog
        from naiyue_test_agent.application.async_application import build_async_local_application
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': a, 'B': b}))
        app = await build_async_local_application('unused', tools=[], system_prompt='', response_format=None)
        first, second = await app.acreate_session('A'), await app.acreate_session('B')
        tasks = [asyncio.create_task(app.arun(first, [('user', 'first')])),
                 asyncio.create_task(app.arun(second, [('user', 'second')]))]
        try:
            await asyncio.wait_for(asyncio.gather(entered_a.wait(), entered_b.wait()), 5)
            app.request_stop(first)
            assert (await asyncio.wait_for(tasks[0], 5))['run_stopped']
            assert not tasks[1].done()
            release_b.set()
            assert (await tasks[1])['messages'][-1].content == 'other session'
        finally:
            release_a.set()
            release_b.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
