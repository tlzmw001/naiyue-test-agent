"""Frozen operation bindings and close during off-loop admission."""
import asyncio
from dataclasses import replace
from threading import Event

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.application.sessions.background_tasks import BackgroundProcessManager
from naiyue_test_agent.tools.processes import ProcessOutcome
from test_background_tasks import submit, wait_status


class Runner:
    async def run(self, request, consume, control=None):
        await consume('stdout', b'original environment')
        return ProcessOutcome(0, 'exited', True, True, True, True, 0)


def test_operation_keeps_environment_when_directory_disappears(tmp_path):
    async def scenario():
        manager = BackgroundProcessManager()
        try:
            row = await submit(manager, tmp_path, 'unused', runner=Runner())
            await wait_status(manager, row['task_id'], lambda value: value['status'] == 'finished')
            item = manager._entries[row['task_id']]
            assert item.environment.root == str(tmp_path)
            assert item.request.execution_environment == item.environment.public()
            tmp_path.rmdir()
            output = await manager.output(row['task_id'], session_id='s', workspace_root=tmp_path)
            assert 'original environment' in output['output']
            assert len((await manager.list(session_id='s', workspace_root=tmp_path))['items']) == 1
            assert (await manager.stop(row['task_id']))['cleanup_confirmed']
        finally:
            await manager.aclose()
    asyncio.run(scenario())


def test_close_during_threaded_admission_prevents_worker_and_capture(monkeypatch, tmp_path):
    from naiyue_test_agent.application.sessions import background_tasks
    async def scenario():
        manager = BackgroundProcessManager()
        loop = asyncio.get_running_loop()
        entered, release, captures = asyncio.Event(), Event(), []
        original = manager._validate_binding
        def blocked(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return original(*args)
        def capture(*args, **kwargs):
            captures.append(True)
            raise AssertionError('Closed admission created capture')
        monkeypatch.setattr(manager, '_validate_binding', blocked)
        monkeypatch.setattr(background_tasks, 'ProcessCapture', capture)
        task = asyncio.create_task(submit(manager, tmp_path, 'unused', runner=Runner()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(manager.aclose(), 5)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(ToolException, match='admission is closed'):
            await task
        assert captures == [] and manager._entries == {} and manager._calls == {}
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['missing', 'binding', 'request'])
def test_unverified_background_environment_never_registers_or_spawns(tmp_path, change):
    from dataclasses import replace
    from runtime_context_helpers import host_files
    from naiyue_test_agent.execution_roots import ExecutionRoot
    from naiyue_test_agent.tools.settings import BashSettings
    from test_process_lifecycle import request
    async def scenario():
        manager = BackgroundProcessManager()
        environment = host_files(tmp_path).environment
        native = request(tmp_path, '')
        if change == 'missing':
            environment = None
        elif change == 'binding':
            environment = replace(environment, binding_id='another-binding')
        else:
            native = replace(native, execution_environment={'kind': 'host'})
        try:
            with pytest.raises(ToolException, match='environment differs'):
                await manager.submit(session_id='S', workspace_root=tmp_path,
                    execution_root=ExecutionRoot(tmp_path), environment=environment,
                    archive_root=tmp_path.parent / 'archives', run_id='R', tool_call_id='C',
                    agent_path='root', command='', name='bash', request=native,
                    settings=BashSettings(), runner=Runner(), control=None)
            assert manager._entries == {} and manager._calls == {}
        finally:
            await manager.aclose()
    asyncio.run(scenario())
