"""Native custom progress must arrive before a real tool finishes."""

from native_application_helpers import async_test

import asyncio
from threading import Event

import pytest
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission, require_tool_permit
from event_sequence import EventSequenceChecker
from test_application_run_events import ToolModel, make_app as make_test_app
from test_async_application import StreamingToolModel, make_app, call


@pytest.mark.parametrize('native_async', [False, True])
def test_progress_arrives_during_unfinished_async_application_tool(monkeypatch, caplog, native_async):
    async def scenario():
        entered, release, arrived = Event(), Event(), Event()
        def body(runtime):
            from naiyue_test_agent.tools.progress import ToolProgress
            require_tool_permit(runtime)
            ToolProgress(runtime).report(phase='working', completed=3, unit='entries', text='PROGRESS_ONLY')
            entered.set()
        if native_async:
            @tool
            async def probe(runtime: ToolRuntime) -> str:
                """Report work then wait for deterministic release."""
                body(runtime)
                assert await asyncio.to_thread(release.wait, 5)
                return 'final result'
        else:
            @tool
            def probe(runtime: ToolRuntime) -> str:
                """Report work then wait for deterministic release."""
                body(runtime)
                assert release.wait(5)
                return 'final result'
        # A runtime-injected tool's exported public schema is the original base.
        from pydantic import BaseModel
        from naiyue_test_agent.tools.schemas import build_tool_schemas
        public, execution = build_tool_schemas('ProbeProgressInput', BaseModel)
        probe.args_schema = execution
        spec = ToolSpec(probe, public, permissions=ToolPermission(default='allow'))
        chosen = StreamingToolModel(replies=[call('probe', {}, 'progress'), AIMessage(content='done')])
        app = await make_app(monkeypatch, chosen, [spec])
        sid = await app.acreate_session('A')
        events = []
        def collect(event):
            events.append(event)
            if event.event_type == 'tool_progress' and event.data.phase == 'working':
                arrived.set()
        app.subscribe_events(collect)
        task = asyncio.create_task(app.arun(sid, [('user', 'run')]))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert await asyncio.to_thread(arrived.wait, 5)
            assert not task.done() and not any(e.event_type == 'tool_call_finished' for e in events)
            release.set()
            result = await asyncio.wait_for(task, 5)
            message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert message.content == 'final result' and message.tool_call_id == 'progress'
            assert all('PROGRESS_ONLY' not in str(m.content) for m in result['messages'])
            assert all('PROGRESS_ONLY' not in str(m.content) for m in chosen.seen[1])
            snapshot = await app._agent.aget_state({'configurable': {
                'thread_id': app._sessions._sessions[sid]['thread_id']}})
            assert 'PROGRESS_ONLY' not in str(snapshot.values)
            EventSequenceChecker.assert_healthy(events, caplog.records)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('concurrency', [1, 2])
@async_test
async def test_async_application_receives_sync_tool_progress_before_return(monkeypatch, caplog, concurrency):
    from pydantic import BaseModel
    from naiyue_test_agent.tools.schemas import build_tool_schemas
    from naiyue_test_agent.tools.progress import ToolProgress
    entered, release, arrived = Event(), Event(), Event()
    @tool
    def probe(runtime: ToolRuntime) -> str:
        """Emit progress through the synchronous runtime writer."""
        assert runtime.config['max_concurrency'] == concurrency
        require_tool_permit(runtime)
        ToolProgress(runtime).report(phase='working', completed=1, unit='entries')
        entered.set()
        assert release.wait(5)
        return 'real result'
    public, execution = build_tool_schemas('SyncProgressInput', BaseModel)
    probe.args_schema = execution
    spec = ToolSpec(probe, public, permissions=ToolPermission(default='allow'))
    model = ToolModel(responses=[call('probe', {}, 'sync'), AIMessage(content='done')])
    app = await make_test_app(monkeypatch, model, [spec])
    app._agent = app._agent.with_config({'max_concurrency': concurrency})
    sid, events = await app.acreate_session('A'), []
    def collect(event):
        events.append(event)
        if event.event_type == 'tool_progress' and event.data.phase == 'working':
            arrived.set()
    app.subscribe_events(collect)
    try:
        task = asyncio.create_task(app.arun(sid, [('user', 'work')], max_concurrency=concurrency))
        try:
            assert await asyncio.to_thread(entered.wait, 5) and await asyncio.to_thread(arrived.wait, 5)
            assert not task.done()
        finally:
            release.set()
        result = await asyncio.wait_for(asyncio.shield(task), 5)
        assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).content == 'real result'
        EventSequenceChecker.assert_healthy(events, caplog.records)
    finally:
        await app.aclose()


def test_queued_progress_does_not_claim_waiting_tool_has_started_io(monkeypatch, caplog):
    async def scenario():
        from pydantic import BaseModel
        from naiyue_test_agent.tools.schemas import build_tool_schemas
        from naiyue_test_agent.tools.progress import ToolProgress
        release, entered, queued = Event(), Event(), Event()
        calls = []
        @tool
        def first(runtime: ToolRuntime) -> str:
            """Hold the sequential slot until deterministic release."""
            ToolProgress(runtime).report(phase='first_io')
            entered.set()
            assert release.wait(5)
            calls.append('first')
            return 'first'
        @tool
        def second(runtime: ToolRuntime) -> str:
            """Run only after the first tool settles."""
            calls.append('second')
            ToolProgress(runtime).report(phase='second_io')
            return 'second'
        public, execution = build_tool_schemas('QueueProgressInput', BaseModel)
        first.args_schema = second.args_schema = execution
        specs = [ToolSpec(item, public, execution_mode='sequential' if item.name == 'first' else 'parallel',
                          permissions=ToolPermission(default='allow')) for item in (first, second)]
        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': item.name, 'args': {}, 'id': item.name} for item in (first, second)]), AIMessage(content='done')])
        app = await make_app(monkeypatch, model, specs)
        sid, events = await app.acreate_session('A'), []
        def collect(event):
            events.append(event)
            if event.event_type == 'tool_progress' and event.data.tool_call_id == 'second' and event.data.activity == 'queued':
                queued.set()
        app.subscribe_events(collect)
        task = asyncio.create_task(app.arun(sid, [('user', 'two calls')], max_concurrency=2))
        try:
            assert await asyncio.to_thread(entered.wait, 5) and await asyncio.to_thread(queued.wait, 5)
            assert not calls and not any(e.event_type == 'tool_progress' and e.data.phase == 'second_io' for e in events)
            release.set()
            await asyncio.wait_for(task, 5)
            assert calls == ['first', 'second']
            EventSequenceChecker.assert_healthy(events, caplog.records)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_broken_progress_receiver_keeps_real_write_and_original_io_failure(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.tools import create_session_write_tool
    from naiyue_test_agent.tools import write as write_module
    from naiyue_test_agent.agent_runtime.tool_execution.observation import ToolProgressObserver
    def fail(*_):
        raise RuntimeError('PRIVATE_OBSERVER_DETAIL')
    monkeypatch.setattr(ToolProgressObserver, 'accept_custom', fail)
    async def scenario():
        model = StreamingToolModel(replies=[call('write', {'path': 'file.txt', 'content': 'written'}, 'one'),
            AIMessage(content='done'), call('write', {'path': 'file.txt', 'content': 'unwritten'}, 'two'),
            AIMessage(content='handled')])
        app = await make_app(monkeypatch, model, [create_session_write_tool()])
        sid, events = await app.acreate_workspace_session('A', tmp_path), []
        app.subscribe_events(events.append)
        try:
            result = await app.arun(sid, [('user', 'write')])
            assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).status == 'success'
            assert (tmp_path / 'file.txt').read_bytes() == b'written'
            failure = OSError('PRIVATE_IO_DETAIL')
            def fsync(_):
                raise failure
            monkeypatch.setattr(write_module.os, 'fsync', fsync)
            result = await app.arun(sid, [('user', 'write again')])
            message = next(m for m in result['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == 'two')
            assert message.status == 'error' and 'I/O' in message.content
            assert (tmp_path / 'file.txt').read_bytes() == b'written'
            assert not list(tmp_path.glob('.naiyue-write-*'))
            assert events[-1].data.status == 'completed' and not app._sessions._sessions[sid]['busy']
            assert 'tool_progress_unavailable' in caplog.text and 'PRIVATE_' not in caplog.text
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('persistent', [False, True])
@pytest.mark.parametrize('native_model', [False, True])
def test_application_variants_use_startup_progress_settings(monkeypatch, tmp_path, persistent, native_model):
    async def scenario():
        from naiyue_test_agent import application
        from naiyue_test_agent.application.async_application import build_async_local_application
        from naiyue_test_agent.application.async_persistent import open_async_persistent_application
        from naiyue_test_agent.model_access import ModelCatalog
        from naiyue_test_agent.tools import ToolSettings, create_session_read_tool
        (tmp_path / 'file.txt').write_text('real content')
        replies = [call('read', {'path': 'file.txt'}, 'read'), AIMessage(content='done')]
        chosen = StreamingToolModel(replies=replies) if native_model else ToolModel(responses=replies)
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': chosen}))
        settings = ToolSettings.model_validate({'progress': {'enabled': False}})
        options = dict(tools=[create_session_read_tool()], system_prompt='', response_format=None, settings=settings)
        owner = open_async_persistent_application('unused', database_path=tmp_path / 'session.sqlite', **options) if persistent else None
        app = await owner.__aenter__() if owner else await build_async_local_application('unused', **options)
        try:
            events = []
            app.subscribe_events(events.append)
            sid = await app.acreate_workspace_session('A', tmp_path)
            result = await app.arun(sid, [('user', 'read')])
            assert app._sessions._progress_settings is settings.progress
            assert not any(e.event_type == 'tool_progress' for e in events)
            assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).content == 'real content'
            assert events[-1].data.status == 'completed'
        finally:
            if owner:
                await owner.__aexit__(None, None, None)
            else:
                await app.aclose()
    asyncio.run(scenario())
