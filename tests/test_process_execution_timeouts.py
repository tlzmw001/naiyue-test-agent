"""Unlimited execution still owns startup, stop, cancellation and cleanup."""

import asyncio
from dataclasses import replace
import os
from types import SimpleNamespace

import pytest

from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessCleanupError
from naiyue_test_agent.tools.settings import ProcessSettings
from test_process_lifecycle import request


def test_unlimited_request_runs_and_closes_real_process(tmp_path):
    async def scenario():
        chunks = []
        async def consume(stream, data):
            chunks.append(data)
        result = await HostProcessRunner().run(replace(request(tmp_path, "print('done')"),
            timeout_seconds=None), consume)
        assert result.termination == 'exited' and b''.join(chunks) == b'done\n'
        assert result.process_reaped and result.pipes_closed and result.consumer_settled
    asyncio.run(scenario())


@pytest.mark.parametrize('finite', [False, True])
def test_startup_user_stop_enters_cleanup_and_keeps_eventual_handle_owned(monkeypatch, tmp_path, finite):
    async def scenario():
        loop = asyncio.get_running_loop()
        spawn = loop.subprocess_exec
        created, release, cleaning = (asyncio.Event() for _ in range(3))
        transports, killed = [], []
        control = SimpleNamespace(requested=False)
        async def delayed(*args, **kwargs):
            result = await spawn(*args, **kwargs)
            transports.append(result[0])
            created.set()
            await release.wait()
            return result
        monkeypatch.setattr(loop, 'subprocess_exec', delayed)
        settle, terminate = HostProcessRunner._settle, HostProcessRunner._terminate
        async def record(self, *args, **kwargs):
            cleaning.set()
            return await settle(self, *args, **kwargs)
        async def kill(transport):
            killed.append(transport.get_pid())
            return await terminate(transport)
        monkeypatch.setattr(HostProcessRunner, '_settle', record)
        monkeypatch.setattr(HostProcessRunner, '_terminate', staticmethod(kill))
        async def consume(*args):
            pass
        task = asyncio.create_task(HostProcessRunner().run(replace(request(tmp_path,
            'import time;time.sleep(60)'), timeout_seconds=600 if finite else None), consume, control))
        try:
            await asyncio.wait_for(created.wait(), 5)
            control.requested = True
            # The startup handle is intentionally withheld. Stop must reach cleanup now.
            await asyncio.wait_for(cleaning.wait(), 2)
            assert not task.done()
            release.set()
            result = await asyncio.wait_for(task, 5)
            assert result.termination == 'user_stop' and result.process_reaped
            assert killed == [transports[0].get_pid()]
            with pytest.raises(ProcessLookupError):
                os.kill(transports[0].get_pid(), 0)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_unlimited_silent_process_observes_stop_without_waiting_for_output(monkeypatch, tmp_path):
    async def scenario():
        loop = asyncio.get_running_loop()
        spawn = loop.subprocess_exec
        started = asyncio.Event()
        handles = []
        async def record(*args, **kwargs):
            result = await spawn(*args, **kwargs)
            handles.append(result[0])
            started.set()
            return result
        monkeypatch.setattr(loop, 'subprocess_exec', record)
        async def consume(*args):
            pytest.fail('This command has no output')
        control = SimpleNamespace(requested=False)
        task = asyncio.create_task(HostProcessRunner().run(replace(request(tmp_path,
            'import time;time.sleep(60)'), timeout_seconds=None), consume, control))
        try:
            await asyncio.wait_for(started.wait(), 5)
            control.requested = True
            result = await asyncio.wait_for(task, 5)
            assert result.termination == 'user_stop' and result.pipes_closed and result.consumer_settled
            with pytest.raises(ProcessLookupError):
                os.kill(handles[0].get_pid(), 0)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('deadline', [None, 105.0])
def test_watch_has_no_hidden_default_deadline_but_honors_explicit_deadline(monkeypatch, deadline):
    async def scenario():
        runner = HostProcessRunner()
        clock, waits = [100.0], []
        control = SimpleNamespace(requested=False)
        protocol = SimpleNamespace(changed=asyncio.Event(), errors=[],
            transport=SimpleNamespace(get_returncode=lambda: None))
        consumer = asyncio.create_task(asyncio.Event().wait())
        async def tick(awaitable, timeout):
            awaitable.close()
            waits.append(timeout)
            clock[0] = 1000000.0
            if deadline is None:
                control.requested = True
            raise TimeoutError()
        # Replace only the module's clock/wait view, not the actual test event loop.
        import naiyue_test_agent.tools.processes as module
        monkeypatch.setattr(module, 'asyncio', SimpleNamespace(get_running_loop=lambda:
            SimpleNamespace(time=lambda: clock[0]), wait_for=tick))
        try:
            result = await runner._watch(protocol, consumer, SimpleNamespace(eof_policy='complete'), control, deadline)
            assert result == ('user_stop' if deadline is None else 'timeout')
            assert len(waits) == 1
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('failure_kind', ['consumer', 'spawn'])
def test_unlimited_execution_keeps_real_timeout_error_identity(monkeypatch, tmp_path, failure_kind):
    async def scenario():
        failure = TimeoutError('owned operation failed')
        if failure_kind == 'spawn':
            async def fail(*args, **kwargs):
                raise failure
            monkeypatch.setattr(asyncio.get_running_loop(), 'subprocess_exec', fail)
        async def consume(*args):
            raise failure
        with pytest.raises(TimeoutError) as caught:
            await HostProcessRunner().run(replace(request(tmp_path, "print('data')"),
                timeout_seconds=None), consume)
        assert caught.value is failure
    asyncio.run(scenario())


def test_unlimited_execution_does_not_disable_cleanup_budget(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def consume(*args):
            entered.set()
            await release.wait()
        task = asyncio.create_task(HostProcessRunner(ProcessSettings(cleanup_timeout_seconds=.1)).run(
            replace(request(tmp_path, "import time;print('ready',flush=True);time.sleep(60)"),
                timeout_seconds=None), consume))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel('original cancellation')
            with pytest.raises(ProcessCleanupError) as caught:
                await asyncio.wait_for(task, 5)
            assert caught.value.facts['process_reaped'] and not caught.value.facts['consumer_settled']
            assert isinstance(caught.value.__cause__, asyncio.CancelledError)
            assert caught.value.__cause__.args == ('original cancellation',)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if task.done() and isinstance(task.exception(), ProcessCleanupError):
                await task.exception().consumer_task
    asyncio.run(scenario())


def test_startup_deadline_is_optional_and_waiting_never_cancels_acquisition(monkeypatch):
    async def scenario():
        import naiyue_test_agent.tools.processes as module
        release = asyncio.Event()
        clock, waits = [100.0], []
        async def acquiring():
            await release.wait()
        spawn = asyncio.create_task(acquiring())
        async def tick(tasks, *, timeout):
            assert tuple(tasks) == (spawn,)
            waits.append(timeout)
            clock[0] = 1000.0
            return set(), {spawn}
        monkeypatch.setattr(module, 'asyncio', SimpleNamespace(get_running_loop=lambda:
            SimpleNamespace(time=lambda: clock[0]), wait=tick))
        try:
            result = await HostProcessRunner()._await_spawn(spawn, None, 105.0)
            assert result == 'timeout' and len(waits) == 1
            assert not spawn.done() and not spawn.cancelled()
        finally:
            release.set()
            await asyncio.gather(spawn)
    asyncio.run(scenario())


def test_startup_stop_preserves_late_acquisition_failure(monkeypatch, tmp_path):
    async def scenario():
        failure = OSError('late acquisition failed')
        started, release, cleaning = (asyncio.Event() for _ in range(3))
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            raise failure
        monkeypatch.setattr(asyncio.get_running_loop(), 'subprocess_exec', delayed)
        original = HostProcessRunner._settle
        async def settle(self, *args, **kwargs):
            cleaning.set()
            return await original(self, *args, **kwargs)
        monkeypatch.setattr(HostProcessRunner, '_settle', settle)
        async def consume(*args):
            pytest.fail('No acquired process')
        control = SimpleNamespace(requested=False)
        task = asyncio.create_task(HostProcessRunner().run(replace(request(tmp_path, ''),
            timeout_seconds=None), consume, control))
        try:
            await asyncio.wait_for(started.wait(), 5)
            control.requested = True
            await asyncio.wait_for(cleaning.wait(), 5)
            release.set()
            with pytest.raises(OSError) as caught:
                await asyncio.wait_for(task, 5)
            assert caught.value is failure
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
