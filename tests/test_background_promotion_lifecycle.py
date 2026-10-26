"""Promotion at the real graph, pending-spawn and cancellation boundaries."""

from runtime_context_helpers import host_files
from dataclasses import replace
import asyncio
import json
from threading import Event

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.application.sessions.background_tasks import BackgroundProcessManager
from naiyue_test_agent.execution_roots import ExecutionRoot
from naiyue_test_agent.tools.processes import ProcessOutcome, HostProcessRunner, ProcessCleanupError
from test_async_application import StreamingToolModel, call
from test_background_tools import background_app, command, tool_result
from test_background_tasks import archive_root, wait_status
from test_background_promotion import foreground


@pytest.mark.parametrize('persistent', [False, True])
def test_root_graph_returns_one_receipt_and_next_round_sees_same_process(monkeypatch, tmp_path, persistent):
    async def scenario():
        script = "import os,time;open('starts','a').write(str(os.getpid())+'\\n');print(os.getpid(),flush=True);time.sleep(60)"
        model = StreamingToolModel(replies=[call('bash', {'command': command(script)}), AIMessage(content='started')])
        async with background_app(monkeypatch, tmp_path, model, persistent=persistent, wait_seconds=1) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            result = await app.host.runs.start(sid, [('user', 'start service')])
            receipt = json.loads(tool_result(result, 'bash').content)
            identity = receipt['task_id']
            ready = await wait_status(app.host.sessions._sessions.background_tasks, identity, lambda r: bool(r['output']))
            assert (tmp_path / 'starts').read_text() == ready['output']
            assert not app.host.sessions._sessions.require(sid).is_busy()
            model.replies.extend([call('task_output', {'task_id': identity}, 'query'), AIMessage(content='alive')])
            second = await app.host.runs.start(sid, [('user', 'query')])
            assert json.loads(tool_result(second, 'task_output').content)['output'] == ready['output']
            assert len([m for m in second['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == 't']) == 1
            assert (tmp_path / 'starts').read_text() == ready['output']
            count = len(model.seen)
            stopped = await app.host.tasks.stop(identity, sid)
            assert stopped['cleanup_confirmed'] and stopped['termination'] == 'user_stop'
            assert len(model.seen) == count
    asyncio.run(scenario())


def test_approval_explains_configured_promotion_before_execution(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': 'printf approved'}), AIMessage(content='done')])
        async with background_app(monkeypatch, tmp_path, model, mode='Default', wait_seconds=7) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            pending = await app.host.runs.start(sid, [('user', 'run')])
            assert pending['approval_requests'][0]['target'] == '[automatic background after 7s; original execution deadline applies] printf approved'
            assert (await app.host.tasks.list())['items'] == []
            result = await app.host.runs.resume_approval(sid, ['approve'])
            assert 'exit_code=0' in tool_result(result, 'bash').content
            assert (await app.host.tasks.list())['items'] == []
    asyncio.run(scenario())


def test_pending_spawn_counts_towards_wait_but_does_not_fake_running(monkeypatch, tmp_path):
    from naiyue_test_agent.tools.processes import HostProcessRunner
    from test_process_lifecycle import request
    from naiyue_test_agent.tools.settings import BashSettings
    async def scenario():
        loop = asyncio.get_running_loop()
        original = loop.subprocess_exec
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def delayed(*args, **kwargs):
            calls.append(1)
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(loop, 'subprocess_exec', delayed)
        manager = BackgroundProcessManager()
        pending = asyncio.create_task(manager.foreground(session_id='s', workspace_root=tmp_path, execution_root=ExecutionRoot(tmp_path), environment=host_files(tmp_path).environment, archive_root=archive_root(tmp_path),
            run_id='r', tool_call_id='c', agent_path='root', command='test', name='bash',
            request=request(tmp_path, 'import time;time.sleep(60)'), settings=BashSettings(),
            runner=HostProcessRunner(), control=None, wait_seconds=1, background_limit=1000,
            on_progress=lambda *_: None))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            row = await asyncio.wait_for(asyncio.shield(pending), 5)
            assert row['status'] == 'starting' and row['started_at'] is None and len(calls) == 1
            release.set()
            await wait_status(manager, row['task_id'], lambda r: r['status'] == 'running')
            assert (await manager.stop(row['task_id']))['cleanup_confirmed']
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await manager.aclose()
    asyncio.run(scenario())


def test_cancel_during_promotion_waits_for_disk_and_process_cleanup(monkeypatch, tmp_path):
    from naiyue_test_agent.tools.process_execution import ProcessCapture
    async def scenario():
        manager = BackgroundProcessManager()
        entered, release = asyncio.Event(), Event()
        loop = asyncio.get_running_loop()
        original = ProcessCapture.promote
        def blocked(self, limit):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return original(self, limit)
        monkeypatch.setattr(ProcessCapture, 'promote', blocked)
        settled = asyncio.Event()
        class Runner:
            async def run(self, req, consume, control):
                req.on_execution_started(loop.time() - 2)
                req.on_started()
                while not control.requested:
                    await asyncio.sleep(0)
                settled.set()
                return ProcessOutcome(None, 'user_stop', True, True, True, True, 0)
        pending = asyncio.create_task(foreground(manager, tmp_path, Runner()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            pending.cancel('original cancellation')
            await asyncio.sleep(0)
            assert not pending.done()
            release.set()
            with pytest.raises(asyncio.CancelledError, match='original cancellation'):
                await asyncio.wait_for(pending, 5)
            assert settled.is_set() and (await manager.list())['items'] == []
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await manager.aclose()
    asyncio.run(scenario())


def test_foreground_cleanup_fault_keeps_identity_and_lost_record(tmp_path):
    async def scenario():
        manager = BackgroundProcessManager()
        fault = ProcessCleanupError(facts={'process_reaped': False})
        class Runner:
            async def run(self, req, consume, control):
                raise fault
        with pytest.raises(ProcessCleanupError) as caught:
            await foreground(manager, tmp_path, Runner())
        assert caught.value is fault
        rows = (await manager.list())['items']
        assert len(rows) == 1 and rows[0]['status'] == 'lost' and not rows[0]['cleanup_confirmed']
        assert manager._entries[rows[0]['task_id']].error is None, 'Do not retain the foreground runtime through its traceback'
        with pytest.raises(ProcessCleanupError):
            await manager.aclose()
    asyncio.run(scenario())


def test_short_managed_command_reports_final_actual_output(monkeypatch, tmp_path):
    from naiyue_test_agent.execution import host_shell as shell
    from naiyue_test_agent.tools import create_session_bash_tool
    from test_shell_task_settings import runtime_for
    async def scenario():
        manager = BackgroundProcessManager()
        monkeypatch.setattr(shell.ShellAdapter, 'resolve_executable', lambda *_: '/test/bash')
        monkeypatch.setattr(shell.ShellAdapter, 'probe_version', lambda *_: '5.2.0')
        async def run(self, req, consume, control=None):
            await consume('stdout', b'actual output')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        monkeypatch.setattr(HostProcessRunner, 'run', run)
        spec = create_session_bash_tool()
        runtime = runtime_for(spec, {'command': 'test'}, tmp_path)
        runtime.context = replace(runtime.context, scope=replace(runtime.context.scope, identity=replace(runtime.context.scope.identity, run_id='r'), capabilities=replace(runtime.context.scope.capabilities, process_tasks=manager)))
        reports = []
        runtime.stream_writer = reports.append
        try:
            result = await spec.tool.coroutine(command='test', runtime=runtime)
            assert 'actual output' in result
            assert any(row['text'] == 'actual output' and row['completed'] == 13
                       for row in reports if row['phase'] == 'capture_cleanup')
        finally:
            await manager.aclose()
    asyncio.run(scenario())


def test_quota_during_promotion_io_is_not_lost_before_receipt(monkeypatch, tmp_path):
    from naiyue_test_agent.tools.process_execution import ProcessCapture
    async def scenario():
        manager = BackgroundProcessManager()
        switched, release = asyncio.Event(), Event()
        loop = asyncio.get_running_loop()
        original = ProcessCapture.promote
        def blocked(self, limit):
            result = original(self, limit)
            loop.call_soon_threadsafe(switched.set)
            assert release.wait(5)
            return result
        monkeypatch.setattr(ProcessCapture, 'promote', blocked)
        class Runner:
            async def run(self, req, consume, control):
                req.on_execution_started(loop.time() - 2)
                req.on_started()
                await switched.wait()
                await consume('stdout', b'quota reached')
                release.set()
                while not control.requested:
                    await asyncio.sleep(0)
                return ProcessOutcome(None, 'user_stop', True, True, True, True, 0)
        try:
            result = await asyncio.wait_for(foreground(manager, tmp_path, Runner(), cap=4), 5)
            assert not isinstance(result, dict), 'A stop already observed before handoff wins'
            assert result.outcome.termination == 'user_stop' and result.archived.saved_bytes == 4
            from langchain_core.tools import ToolException
            from naiyue_test_agent.tools.process_execution import format_process_outcome
            with pytest.raises(ToolException, match='background archive limit') as caught:
                format_process_outcome(result.outcome, result.capture.output, result.archived,
                    label='Bash', stop_reason=result.stop_reason)
            assert 'by the user' not in str(caught.value)
        finally:
            release.set()
            await manager.aclose()
    asyncio.run(scenario())
