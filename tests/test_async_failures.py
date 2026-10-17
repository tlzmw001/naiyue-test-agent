"""Async failure cleanup must settle work and keep partial state explicit."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import asyncio
import threading

import pytest
from langchain.tools import tool, ToolRuntime
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel, make_app, call


def test_parallel_fatal_waits_for_other_started_tool_before_terminal(monkeypatch):
    async def scenario():
        entered, failed = asyncio.Event(), asyncio.Event()
        release, settled = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = RuntimeError('synthetic fatal')
        from naiyue_test_agent.agent_runtime.tool_execution.observation import _record_tool_call
        real_dispatch = _record_tool_call.awrap_tool_call

        async def broken_dispatch(request, handler):
            if request.tool_call['name'] == 'fail':
                await entered.wait()
                raise original
            return await real_dispatch(request, handler)

        monkeypatch.setattr(_record_tool_call, 'awrap_tool_call', broken_dispatch)

        @tool
        async def fail() -> str:
            """Fail only once its peer is already doing IO."""
            return 'unreachable after the dispatch fault'

        @tool
        def slow() -> str:
            """Perform already-started synthetic blocking IO."""
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            settled.set()
            return 'settled'

        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'fail', 'args': {}, 'id': 'failure'}, {'name': 'slow', 'args': {}, 'id': 'slow'},
        ])])
        app = await make_app(monkeypatch, model, [ToolSpec(fail, fail.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(slow, slow.args_schema, permissions=ToolPermission(default='allow'))])
        session = await app.acreate_session('A')
        events = []

        def observe(event):
            events.append(event)
            if event.event_type == 'tool_call_finished' and event.data.tool_call_id == 'failure':
                failed.set()

        app.subscribe_events(observe)
        task = asyncio.create_task(app.arun(session, [('user', 'run')]))
        try:
            await asyncio.wait_for(failed.wait(), 5)
            # The peer is held by an Event; this is a liveness deadline, not a timing race.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 1)
            assert app._sessions._sessions[session]['busy']
            assert not any(e.event_type == 'run_finished' for e in events)
            release.set()
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is original and settled.is_set()
            assert events[-1].data.status == 'failed'
            assert app._sessions._sessions[session]['manual_review_required']
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
