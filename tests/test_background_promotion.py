"""One process and deadline survive the foreground/background handoff."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from naiyue_test_agent.application.sessions.background_tasks import BackgroundProcessManager
from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessOutcome
from naiyue_test_agent.tools.settings import BashSettings
from test_process_lifecycle import request
from test_background_tasks import wait_status, archive_root


async def foreground(manager, root, runner, *, timeout=None, control=None, progress=None, settings=None, cap=1000):
    return await manager.foreground(session_id='s', workspace_root=root, archive_root=archive_root(root), run_id='r',
        tool_call_id='c', agent_path='root', command='test', name='bash',
        request=replace(request(root, ''), timeout_seconds=timeout),
        settings=settings or BashSettings(), runner=runner, control=control,
        wait_seconds=1, background_limit=cap, on_progress=progress or (lambda *_: None))


@pytest.mark.parametrize('timeout,promotes', [(None, True), (3, True), (1, False), (.5, False)])
def test_common_clock_and_deadline_priority(tmp_path, timeout, promotes):
    async def scenario():
        manager = BackgroundProcessManager()
        entered, release = asyncio.Event(), asyncio.Event()
        controls, reports = [], []
        class Runner:
            async def run(self, req, consume, control):
                controls.append(control)
                req.on_execution_started(asyncio.get_running_loop().time() - 2)
                req.on_started()
                await consume('stdout', b'before')
                entered.set()
                await release.wait()
                req.on_exited()
                return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        origin = SimpleNamespace(requested=False)
        pending = asyncio.create_task(foreground(manager, tmp_path, Runner(), timeout=timeout,
            control=origin, progress=lambda *v: reports.append(v)))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            if promotes:
                receipt = await asyncio.wait_for(asyncio.shield(pending), 5)
                identity = receipt['task_id']
                assert controls[0] is not origin
                origin.requested = True
                assert not controls[0].requested
                count = len(reports)
                release.set()
                done = await wait_status(manager, identity, lambda r: r['status'] == 'finished')
                assert done['output'] == 'before' and done['cleanup_confirmed']
                assert len(reports) == count
            else:
                # Let the waiter observe the expired F; T <= F still never hands off.
                for _ in range(5):
                    await asyncio.sleep(0)
                assert not pending.done() and (await manager.list())['items'] == []
                release.set()
                result = await asyncio.wait_for(pending, 5)
                assert result.outcome.returncode == 0
                assert (await manager.list())['items'] == []
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await manager.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('reason', ['exit', 'stop', 'cancel'])
def test_handoff_loses_to_exit_stop_and_cancellation(tmp_path, reason):
    async def scenario():
        manager = BackgroundProcessManager()
        entered, release = asyncio.Event(), asyncio.Event()
        control = SimpleNamespace(requested=False)
        class Runner:
            async def run(self, req, consume, owned):
                req.on_execution_started(asyncio.get_running_loop().time() - 2)
                req.on_started()
                if reason == 'exit':
                    req.on_exited()
                elif reason == 'stop':
                    control.requested = True
                entered.set()
                await release.wait()
                return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        pending = asyncio.create_task(foreground(manager, tmp_path, Runner(), control=control))
        await asyncio.wait_for(entered.wait(), 3)
        if reason == 'cancel':
            pending.cancel()
        release.set()
        if reason == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            assert (await pending).outcome.returncode == 0
        assert (await manager.list())['items'] == []
        await manager.aclose()
    asyncio.run(scenario())


def test_archive_promotion_preserves_gap_and_resumes_same_file(tmp_path):
    from naiyue_test_agent.tools.process_execution import ProcessCapture
    capture = ProcessCapture(tmp_path, BashSettings(max_archive_bytes=4, max_output_bytes=2))
    capture.record(b'012345')
    original = capture.snapshot()
    assert original['archive_saved_bytes'] == 4
    assert capture.promote(10) is None
    assert capture.record(b'after') is None
    saved = capture.finish()
    assert not saved.complete and saved.reason == 'command_limit'
    assert saved.saved_bytes == 9 and saved.paths == original['archive_paths']
    assert (tmp_path / saved.paths['raw']).read_bytes() == b'0123after'


def test_smaller_background_quota_stops_without_truncating_existing_file(tmp_path):
    from naiyue_test_agent.tools.process_execution import ProcessCapture
    capture = ProcessCapture(tmp_path, BashSettings(max_archive_bytes=20, max_output_bytes=2))
    capture.record(b'012345')
    assert capture.promote(4) == 'command_limit'
    saved = capture.finish()
    assert saved.saved_bytes == 6
    assert (tmp_path / saved.paths['raw']).read_bytes() == b'012345'


def test_actual_process_promotes_once_and_keeps_original_timeout(tmp_path):
    async def scenario():
        manager = BackgroundProcessManager()
        class Runner(HostProcessRunner):
            calls = 0
            async def run(self, req, consume, control=None):
                self.calls += 1
                return await super().run(req, consume, control)
        runner = Runner()
        script = "import os,time;print(os.getpid(),flush=True);time.sleep(60)"
        req = replace(request(tmp_path, script), timeout_seconds=3, merge_output=True)
        try:
            receipt = await manager.foreground(session_id='s', workspace_root=tmp_path, archive_root=archive_root(tmp_path),
                run_id='r', tool_call_id='c', agent_path='root', command=script, name='bash',
                request=req, settings=BashSettings(), runner=runner, control=None,
                wait_seconds=1, background_limit=1000, on_progress=lambda *_: None)
            assert runner.calls == 1 and receipt['status'] == 'running'
            identity = receipt['task_id']
            initial = await manager.output(identity)
            done = await wait_status(manager, identity, lambda r: r['status'] == 'finished')
            assert done['termination'] == 'timeout' and done['cleanup_confirmed']
            assert done['output'] == initial['output']
            assert done['archive_paths'] == initial['archive_paths']
            assert done['archive_saved_bytes'] == initial['archive_saved_bytes'] > 0
            assert runner.calls == 1
        finally:
            await manager.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('name', ['bash', 'powershell'])
@pytest.mark.parametrize('agent', ['root', 'root/child'])
def test_both_shells_only_promote_root_even_if_child_has_port(monkeypatch, tmp_path, name, agent):
    from naiyue_test_agent.tools import create_builtin_tools, shell
    from naiyue_test_agent.settings import AppSettings
    from test_shell_task_settings import runtime_for
    from naiyue_test_agent.shell_task_settings import ShellTaskSettings
    import json
    async def scenario():
        manager = BackgroundProcessManager()
        release = asyncio.Event()
        monkeypatch.setattr(shell.ShellAdapter, 'resolve_executable', lambda *_: '/test/interpreter')
        monkeypatch.setattr(shell.ShellAdapter, 'probe_version', lambda *_: '7.4.0')
        async def run(self, req, consume, control=None):
            if req.on_execution_started is not None:
                req.on_execution_started(asyncio.get_running_loop().time() - 2)
                req.on_started()
                await release.wait()
            await consume('stdout', b'finished')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        monkeypatch.setattr(shell.HostProcessRunner, 'run', run)
        spec = next(s for s in create_builtin_tools(AppSettings().tools,
            shell_task_settings=ShellTaskSettings(foreground_wait_seconds=1)) if s.tool.name == name)
        args = {'command': 'test'}
        runtime = runtime_for(spec, args, tmp_path)
        runtime.context = replace(runtime.context, scope=replace(runtime.context.scope, identity=replace(runtime.context.scope.identity, agent_path=agent, run_id='r'), capabilities=replace(runtime.context.scope.capabilities, process_tasks=manager)))
        runtime.context.authorization.permit['agent_path'] = agent
        reports = []
        runtime.stream_writer = reports.append
        try:
            result = await spec.tool.coroutine(**args, runtime=runtime)
            if agent == 'root':
                identity = json.loads(result)['task_id']
                count = len(reports)
                release.set()
                await wait_status(manager, identity, lambda r: r['status'] == 'finished')
                assert len(reports) == count
            else:
                assert 'exit_code=0' in result and 'finished' in result
                assert (await manager.list())['items'] == []
                assert spec.permissions.prepare(args, runtime.context)['target'] == 'test'
        finally:
            release.set()
            await manager.aclose()
    asyncio.run(scenario())
