"""All seven existing builtins retain real IO and approval in async dispatch."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import asyncio
import threading

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import tools as tool_factories
from test_async_application import StreamingToolModel, make_app, call


def test_seven_builtins_share_async_batch_and_bash_approval(monkeypatch, tmp_path):
    async def scenario():
        (tmp_path / 'note.txt').write_text('original\n')
        operations = [
            ('read', {'path': 'note.txt'}), ('ls', {'path': '.'}),
            ('find', {'pattern': '*.txt', 'path': '.'}),
            ('grep', {'pattern': 'original', 'path': 'note.txt'}),
            ('write', {'path': 'note.txt', 'content': 'updated\n'}),
            ('edit', {'path': 'note.txt', 'edits': [{'oldText': 'updated', 'newText': 'final'}]}),
            ('read', {'path': 'note.txt'}), ('bash', {'command': "printf 'done'"}),
        ]
        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': name, 'args': args, 'id': str(i)} for i, (name, args) in enumerate(operations)
        ]), AIMessage(content='done')])
        tools = [getattr(tool_factories, f'create_session_{name}_tool')()
                 for name in ('read', 'ls', 'find', 'grep', 'write', 'edit', 'bash')]
        app = await make_app(monkeypatch, model, tools)
        session = await app.host.sessions.create_workspace('A', tmp_path)
        try:
            paused = await app.host.runs.start(session, [('user', 'synthetic IO')])
            assert [item['tool_name'] for item in paused['approval_requests']] == ['bash']
            assert (tmp_path / 'note.txt').read_text() == 'original\n'
            result = await app.host.runs.resume_approval(session, ['approve'])
            outputs = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [m.tool_call_id for m in outputs] == [str(i) for i in range(8)]
            assert all(m.status == 'success' for m in outputs)
            assert outputs[0].content == 'original\n' and outputs[6].content == 'final\n'
            assert 'note.txt' in outputs[1].content and 'note.txt' in outputs[2].content
            assert 'original' in outputs[3].content
            assert 'exit_code=0' in outputs[7].content and 'done' in outputs[7].content
            assert (tmp_path / 'note.txt').read_text() == 'final\n'
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_async_validation_error_returns_tool_message_and_continues(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[call('read', {'path': 24}), AIMessage(content='corrected')])
        app = await make_app(monkeypatch, model, [tool_factories.create_session_read_tool()])
        session = await app.host.sessions.create_workspace('A', tmp_path)
        try:
            result = await app.host.runs.start(session, [('user', 'bad args')])
            error = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert error.status == 'error' and 'Invalid read arguments' in error.content
            assert result['messages'][-1].content == 'corrected'
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_sync_tool_runs_off_loop_and_cancel_waits_for_settlement(monkeypatch):
    async def scenario():
        entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()
        loop, loop_thread, tool_threads = asyncio.get_running_loop(), threading.get_ident(), []

        @tool
        def blocking() -> str:
            """Complete a synthetic blocking operation before its owner is released."""
            tool_threads.append(threading.get_ident())
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            finished.set()
            return 'finished'

        model = StreamingToolModel(replies=[call('blocking', {})])
        app = await make_app(monkeypatch, model, [ToolSpec(blocking, blocking.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))])
        session = await app.host.sessions.create('A')
        events = []
        app.host.observability.subscribe(events.append)
        task = asyncio.create_task(app.host.runs.start(session, [('user', 'block')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert tool_threads == [tool_threads[0]] and tool_threads[0] != loop_thread
            task.cancel()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 1)
            assert app.host.sessions._sessions.require(session).describe_session()['busy']
            assert not any(e.event_type == 'run_finished' for e in events)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set() and not app.host.sessions._sessions.require(session).describe_session()['busy']
            assert events[-1].data.status == 'failed' and events[-1].data.manual_review_required
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
