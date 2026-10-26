"""The actual Bash registration uses native async IO and the shared runner."""

import asyncio
import threading

from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.tools.bash import create_session_bash_tool
from naiyue_test_agent.tools.settings import ProcessSettings
from naiyue_test_agent.settings import AppSettings, load_settings
from test_async_application import StreamingToolModel, make_app, call


def test_bash_has_native_async_entry_and_shared_process_settings(tmp_path, monkeypatch):
    from naiyue_test_agent.execution import host_processes as module
    from naiyue_test_agent.execution.host_shell import ShellAdapter
    from runtime_context_helpers import typed_context, RunIdentity, ExecutionRoot
    from tool_feedback_helpers import invoke_tool_feedback
    captured = []
    original = module.HostProcessRunner
    def factory(settings):
        captured.append(settings)
        return original(settings)
    monkeypatch.setattr(module, 'HostProcessRunner', factory)
    settings = AppSettings(process=ProcessSettings(max_pending_output_bytes=4096))
    specs = create_builtin_tools(settings.tools, process_settings=settings.process, shell_task_settings=settings.shell_tasks, progress_settings=settings.progress)
    bash = next(spec for spec in specs if spec.tool.name == 'bash')
    assert bash.tool.coroutine is not None
    assert captured == []
    monkeypatch.setattr(ShellAdapter, 'resolve_executable', lambda *_: '/owned/interpreter')
    monkeypatch.setattr(ShellAdapter, 'probe_version', lambda *_: '7.4.0')
    async def execute(*args, **kwargs):
        return 'done'
    monkeypatch.setattr(module, 'execute_process_request', execute)
    for spec in (spec for spec in specs if spec.tool.name in ('bash', 'powershell')):
        result = asyncio.run(invoke_tool_feedback(spec, {'command': 'text'}, call_id=spec.tool.name,
            context=typed_context(identity=RunIdentity('S', execution_root=ExecutionRoot(tmp_path))),
            permission_mode='BypassApprovals'))
        assert result.status == 'success' and result.content == 'done'
    assert captured == [settings.process, settings.process]
    assert set(bash.tool.tool_call_schema.model_json_schema()['properties']) == {'command', 'timeout', 'background'}


def test_async_bash_timeout_is_tool_error_and_next_model_runs(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.tools.processes import HostProcessRunner
        original = HostProcessRunner.run
        loop_thread, runner_threads = threading.get_ident(), []
        async def record_thread(self, request, *args, **kwargs):
            if request.argv[-1] != '--version':
                runner_threads.append(threading.get_ident())
            return await original(self, request, *args, **kwargs)
        monkeypatch.setattr(HostProcessRunner, 'run', record_thread)
        model = StreamingToolModel(replies=[call('bash', {'command': 'printf EARLY; sleep 60', 'timeout': 1}, 'call-1'),
                                            AIMessage(content='inspect before retry')])
        app = await make_app(monkeypatch, model, [create_session_bash_tool()])
        sid = await app.host.sessions.create_workspace('A', tmp_path)
        events = []
        app.host.observability.subscribe(events.append)
        try:
            pending = await app.host.runs.start(sid, [('user', 'run')])
            assert pending['approval_requests']
            result = await app.host.runs.resume_approval(sid, ['approve'])
            message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert message.tool_call_id == 'call-1' and message.status == 'error'
            assert 'timed out' in message.content and 'EARLY' in message.content
            assert 'output_complete=' in message.content
            assert result['messages'][-1].content == 'inspect before retry'
            assert not app.host.sessions._sessions.require(sid).describe_session()['manual_review_required']
            finished = [e for e in events if e.event_type == 'tool_call_finished']
            assert finished[-1].data.outcome == 'error_result'
            assert events[-1].data.status == 'completed'
            assert runner_threads == [loop_thread]
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_settings_snapshot_validates_process_budgets_without_affecting_permissions(tmp_path, caplog):
    path = tmp_path / 'settings.toml'
    path.write_text('[permissions]\nmode="NoAsk"\n[process]\ncleanup_timeout_seconds=2.0\nmax_pending_output_bytes=4096\n')
    valid = load_settings(path)
    assert valid.process.cleanup_timeout_seconds == 2 and valid.process.max_pending_output_bytes == 4096
    path.write_text('[permissions]\nmode="NoAsk"\n[process]\nmax_pending_output_bytes=0\n')
    fallback = load_settings(path)
    assert fallback.process == ProcessSettings() and fallback.permissions.mode == 'NoAsk'
    assert 'process_settings_fallback' in caplog.text


def test_user_stop_skips_later_serial_command_after_whole_batch_approval(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.tools.processes import HostProcessRunner
        started = asyncio.Event()
        commands = []
        original = HostProcessRunner.run
        async def record(self, req, consume, control=None):
            if req.argv[-1] == '--version':
                return await original(self, req, consume, control)
            commands.append(req.argv[-1])
            async def observed(stream, data):
                await consume(stream, data)
                started.set()
            return await original(self, req, observed, control)
        monkeypatch.setattr(HostProcessRunner, 'run', record)
        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'bash', 'id': 'first', 'args': {'command': 'printf started; sleep 60'}},
            {'name': 'bash', 'id': 'second', 'args': {'command': 'printf wrong > forbidden'}},
        ]), AIMessage(content='must not run')])
        app = await make_app(monkeypatch, model, [create_session_bash_tool()])
        sid = await app.host.sessions.create_workspace('A', tmp_path)
        task = None
        try:
            paused = await app.host.runs.start(sid, [('user', 'two commands')])
            assert len(paused['approval_requests']) == 2 and commands == []
            task = asyncio.create_task(app.host.runs.resume_approval(sid, ['approve', 'approve']))
            await asyncio.wait_for(started.wait(), 5)
            app.host.runs.stop(sid)
            result = await asyncio.wait_for(task, 5)
            messages = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [m.tool_call_id for m in messages] == ['first', 'second']
            assert all(m.status == 'error' for m in messages)
            assert 'stopped by the user' in messages[0].content
            assert messages[1].additional_kwargs['naiyue_not_executed'] == 'user_stop'
            assert commands == ['printf started; sleep 60'] and not (tmp_path / 'forbidden').exists()
            assert result['run_stopped'] and len(model.seen) == 1
        finally:
            if app.host.sessions._sessions.require(sid).describe_session()['busy']:
                app.host.runs.stop(sid)
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
