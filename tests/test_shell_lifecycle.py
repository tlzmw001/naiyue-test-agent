"""Shell archive ownership during cancellation and protected cleanup failure."""

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest


def runtime_for(root):
    from naiyue_test_agent.tools.permissions import execution_environment
    return SimpleNamespace(tool_call_id='shell-call', context={
        'session_id': 's', 'workspace_root': root, 'archive_root': root.parent / (root.name + '-archive'),
        '_execution_permit': {'session_id': 's', 'agent_path': 'root', 'call_id': 'shell-call',
            'subject': {'workspace': str(root), 'execution_environment': execution_environment()}},
    })


def test_cancellation_during_archive_finish_waits_for_owned_io(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.tools import create_session_bash_tool
        from naiyue_test_agent.tools.output import ShellOutputArchive
        from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessOutcome
        tool = create_session_bash_tool()
        entered, release, finished = asyncio.Event(), Event(), Event()
        loop = asyncio.get_running_loop()
        original = ShellOutputArchive.finish
        def blocked(self, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            try:
                return original(self, **kwargs)
            finally:
                finished.set()
        async def run(self, request, consume, control):
            await consume('stdout', b'captured')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        monkeypatch.setattr(ShellOutputArchive, 'finish', blocked)
        monkeypatch.setattr(HostProcessRunner, 'run', run)
        task = asyncio.create_task(tool.tool.coroutine('printf ok', runtime_for(tmp_path)))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel('original stop')
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not task.done(), 'Archive writer must settle before cancellation escapes'
            release.set()
            with pytest.raises(asyncio.CancelledError, match='original stop'):
                await task
            assert finished.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_cleanup_fault_identity_and_unsettled_writer_defers_finish(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.tools import create_session_bash_tool
        from naiyue_test_agent.tools.output import ShellOutputArchive
        from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessCleanupError
        tool = create_session_bash_tool()
        release, finalized = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        original = ShellOutputArchive.finish
        facts = []
        fault = ProcessCleanupError(facts={'consumer_settled': False})
        async def writer(consume):
            await release.wait()
            await consume('stdout', b'late')
        async def run(self, request, consume, control):
            fault.consumer_task = asyncio.create_task(writer(consume))
            raise fault
        def finish(self, **kwargs):
            result = original(self, **kwargs)
            facts.append(result)
            loop.call_soon_threadsafe(finalized.set)
            return result
        monkeypatch.setattr(HostProcessRunner, 'run', run)
        monkeypatch.setattr(ShellOutputArchive, 'finish', finish)
        with pytest.raises(ProcessCleanupError) as caught:
            await tool.tool.coroutine('printf ok', runtime_for(tmp_path))
        assert caught.value is fault and facts == []
        release.set()
        await asyncio.wait_for(finalized.wait(), 5)
        assert len(facts) == 1 and facts[0].reason == 'capture_incomplete'
    asyncio.run(scenario())


def test_spawn_fault_becomes_specific_tool_exception_after_finalize(monkeypatch, tmp_path, caplog):
    async def scenario():
        from naiyue_test_agent.tools import create_session_bash_tool
        from naiyue_test_agent.tools.processes import HostProcessRunner
        from langchain_core.tools import ToolException
        tool = create_session_bash_tool()
        async def fail(*args):
            raise OSError('PRIVATE_OS_DETAIL')
        monkeypatch.setattr(HostProcessRunner, 'run', fail)
        with pytest.raises(ToolException, match='could not start or capture'):
            await tool.tool.coroutine('printf ok', runtime_for(tmp_path))
        assert 'PRIVATE_OS_DETAIL' not in caplog.text and 'shell_process_failed' in caplog.text
    asyncio.run(scenario())
