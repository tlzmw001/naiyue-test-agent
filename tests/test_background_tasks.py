"""Application-owned processes outlive their submitting run and settle explicitly."""

import asyncio
from dataclasses import replace
import os
from types import SimpleNamespace

import pytest

from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessCleanupError
from naiyue_test_agent.tools.settings import BashSettings
from test_process_lifecycle import request


def archive_root(root):
    return root.parent / (root.name + '-archive')


async def submit(manager, root, script, *, call='c', sid='s', settings=None, timeout=None, runner=None):
    return await manager.submit(session_id=sid, workspace_root=root, archive_root=archive_root(root), run_id='r',
        tool_call_id=call, agent_path='root', command=script, name='bash',
        request=replace(request(root, script), timeout_seconds=timeout, merge_output=True),
        settings=settings or BashSettings(), runner=runner or HostProcessRunner(), control=None)


async def wait_status(manager, identity, predicate):
    async def poll():
        while True:
            result = await manager.output(identity)
            if predicate(result):
                return result
            await asyncio.sleep(.01)
    return await asyncio.wait_for(poll(), 10)


def test_registered_process_keeps_running_and_stop_confirms_cleanup(tmp_path):
    from naiyue_test_agent.application.background_tasks import BackgroundProcessManager
    async def scenario():
        manager = BackgroundProcessManager()
        try:
            row = await submit(manager, tmp_path, "import os,time;print(os.getpid(),flush=True);time.sleep(60)")
            assert row['status'] == 'starting'
            ready = await wait_status(manager, row['task_id'], lambda r: bool(r['output']))
            pid = int(ready['output'])
            assert ready['status'] == 'running' and ready['origin_run_id'] == 'r'
            assert ready['archive_paths'] and ready['archive_saved_bytes'] > 0
            assert (await manager.list(session_id='s'))['items'][0]['task_id'] == row['task_id']
            stopped = await manager.stop(row['task_id'], session_id='s', workspace_root=tmp_path)
            assert stopped['status'] == 'finished' and stopped['termination'] == 'user_stop'
            assert stopped['cleanup_confirmed'] and stopped['output_complete']
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            assert (await manager.stop(row['task_id'])) == stopped
        finally:
            await manager.aclose()
    asyncio.run(scenario())


def test_quota_stops_only_its_task_and_keeps_bounded_tail(tmp_path):
    from naiyue_test_agent.application.background_tasks import BackgroundProcessManager
    async def scenario():
        manager = BackgroundProcessManager()
        try:
            quiet = await submit(manager, tmp_path, 'import time;time.sleep(60)', call='quiet')
            noisy = await submit(manager, tmp_path,
                "import os,time;os.write(1,b'A'*5000);time.sleep(60)", call='noisy',
                settings=BashSettings(max_archive_bytes=100, max_output_bytes=20))
            result = await wait_status(manager, noisy['task_id'], lambda r: r['status'] == 'finished')
            assert result['termination'] == 'output_limit' and result['cleanup_confirmed']
            assert result['archive_saved_bytes'] == 100 and not result['archive_complete']
            assert len(result['output'].encode()) <= 20
            assert (await manager.output(quiet['task_id']))['status'] in ('starting', 'running')
        finally:
            await manager.aclose()
        assert all(row['cleanup_confirmed'] for row in (await manager.list())['items'])
    asyncio.run(scenario())


def test_scope_dedup_and_changed_identity_never_spawn_twice(tmp_path):
    from langchain_core.tools import ToolException
    from naiyue_test_agent.application.background_tasks import BackgroundProcessManager
    async def scenario():
        manager = BackgroundProcessManager()
        try:
            first = await submit(manager, tmp_path, 'import time;time.sleep(60)')
            same = await submit(manager, tmp_path, 'import time;time.sleep(60)')
            assert first['task_id'] == same['task_id']
            with pytest.raises(ToolException, match='identity'):
                await submit(manager, tmp_path, "print('different')")
            for operation in (manager.output, manager.stop):
                with pytest.raises(ToolException, match='scope'):
                    await operation(first['task_id'], session_id='other', workspace_root=tmp_path)
            assert (await manager.list(session_id='other'))['items'] == []
            with pytest.raises(ToolException, match='root'):
                await manager.submit(session_id='s', workspace_root=tmp_path, archive_root=archive_root(tmp_path), run_id='r',
                    tool_call_id='child', agent_path='child', command='test', name='bash',
                    request=request(tmp_path, ''), settings=BashSettings(), runner=HostProcessRunner(), control=None)
            assert len((await manager.list())['items']) == 1
        finally:
            await manager.aclose()
    asyncio.run(scenario())


def test_frozen_request_and_empty_worker_context(monkeypatch, tmp_path):
    from naiyue_test_agent.application.background_tasks import BackgroundProcessManager
    from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services, current_run_services
    from naiyue_test_agent.tools.processes import ProcessOutcome
    async def scenario():
        manager = BackgroundProcessManager()
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        class Runner:
            async def run(self, req, consume, control=None):
                entered.set()
                await release.wait()
                seen.append((current_run_services(), req.env.get('SAMPLE'), list(req.argv), control))
                req.on_started()
                return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        req = request(tmp_path, '')
        req.env['SAMPLE'] = 'original'
        origin = SimpleNamespace(requested=False)
        with bind_run_services(RunServices(control=origin)):
            row = await manager.submit(session_id='s', workspace_root=tmp_path, archive_root=archive_root(tmp_path), run_id='r',
                tool_call_id='c', agent_path='root', command='test', name='bash', request=req,
                settings=BashSettings(), runner=Runner(), control=origin)
        await entered.wait()
        req.env['SAMPLE'] = 'changed'
        req.argv.clear()
        origin.requested = True
        release.set()
        result = await wait_status(manager, row['task_id'], lambda r: r['status'] == 'finished')
        assert seen[0][0] is None and seen[0][1] == 'original' and seen[0][2]
        assert seen[0][3] is not origin and not seen[0][3].requested
        assert result['termination'] == 'exited'
        await manager.aclose()
    asyncio.run(scenario())


def test_configured_deadline_and_missing_executable_have_distinct_results(tmp_path):
    from naiyue_test_agent.application.background_tasks import BackgroundProcessManager
    async def scenario():
        manager = BackgroundProcessManager()
        try:
            row = await submit(manager, tmp_path, 'import time;time.sleep(60)', timeout=.1)
            result = await wait_status(manager, row['task_id'], lambda r: r['status'] == 'finished')
            assert result['termination'] == 'timeout' and result['cleanup_confirmed']
            req = replace(request(tmp_path, ''), argv=['/nonexistent/background-executable'])
            row = await manager.submit(session_id='s', workspace_root=tmp_path, archive_root=archive_root(tmp_path), run_id='r',
                tool_call_id='fail', agent_path='root', command='test', name='bash', request=req,
                settings=BashSettings(), runner=HostProcessRunner(), control=None)
            result = await wait_status(manager, row['task_id'], lambda r: r['status'] == 'finished')
            assert result['termination'] == 'start_failed' and result['exit_code'] is None
            assert result['cleanup_confirmed'] and result['error_type'] == 'FileNotFoundError'
        finally:
            await manager.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('entry', ['0', '-1', 'true', '"100"', '1.5'])
def test_background_log_budget_is_strict_and_positive(tmp_path, entry):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[shell_tasks]\nmax_background_archive_bytes=' + entry)
    with pytest.raises(ValueError, match='Shell task settings are invalid'):
        load_settings(path)


def test_background_defaults_and_strict_model_flag():
    from pydantic import ValidationError
    from naiyue_test_agent.shell_task_settings import ShellTaskSettings
    from naiyue_test_agent.tools.shell import ShellInput
    assert ShellTaskSettings().max_background_archive_bytes == 64 * 1024 * 1024
    assert not ShellInput().background
    for invalid in (0, 1, 'true', None):
        with pytest.raises(ValidationError):
            ShellInput(background=invalid)


def test_list_pagination_and_pre_handoff_stop_are_bounded(tmp_path):
    from langchain_core.tools import ToolException
    from naiyue_test_agent.application.background_tasks import BackgroundProcessManager
    from naiyue_test_agent.tools.processes import ProcessOutcome
    async def scenario():
        manager = BackgroundProcessManager()
        count = []
        class Runner:
            async def run(self, req, consume, control=None):
                count.append(req)
                req.on_started()
                return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        runner = Runner()
        for i in range(3):
            await submit(manager, tmp_path, 'pass', call=str(i), runner=runner)
        first = await manager.list(limit=2)
        second = await manager.list(limit=2, before=first['next_before'])
        assert len(first['items']) == 2 and len(second['items']) == 1
        assert second['next_before'] is None
        assert len({row['task_id'] for row in first['items'] + second['items']}) == 3
        with pytest.raises(ToolException, match='stopped before handoff'):
            await manager.submit(session_id='s', workspace_root=tmp_path, archive_root=archive_root(tmp_path), run_id='r',
                tool_call_id='stopped', agent_path='root', command='pass', name='bash',
                request=request(tmp_path, ''), settings=BashSettings(), runner=runner,
                control=SimpleNamespace(requested=True))
        await manager.aclose()
        assert len(count) == 3
    asyncio.run(scenario())


def test_child_shell_request_is_refused_before_submission(tmp_path):
    from langchain_core.tools import ToolException
    from naiyue_test_agent.tools import create_session_bash_tool
    from test_shell_task_settings import runtime_for
    async def scenario():
        spec = create_session_bash_tool()
        args = {'command': 'must not execute', 'background': True, 'timeout': None}
        runtime = runtime_for(spec, args, tmp_path)
        runtime.context['agent_path'] = 'child'
        runtime.context['_execution_permit']['agent_path'] = 'child'
        class Port:
            async def submit(self, **kwargs):
                pytest.fail('Child requests cannot reach background submission')
        runtime.context['_process_tasks'] = Port()
        with pytest.raises(ToolException, match='root'):
            await spec.tool.coroutine(**args, runtime=runtime)
    asyncio.run(scenario())
