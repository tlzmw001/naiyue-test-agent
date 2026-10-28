"""Spawn cancellation, consumer errors, and explicit unconfirmed cleanup facts."""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

from naiyue_test_agent.tools import processes as module
from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessCleanupError
from naiyue_test_agent.tools.settings import ProcessSettings
from naiyue_test_agent.tools.process_protocol import ProcessProtocol
from test_process_lifecycle import request


def test_normal_eof_keeps_consumer_in_execution_phase(monkeypatch, tmp_path):
    async def scenario():
        entered, release, eof, cleanup_entered = (asyncio.Event() for _ in range(4))
        connection_lost = ProcessProtocol.connection_lost
        def closed(self, error):
            connection_lost(self, error)
            eof.set()
        monkeypatch.setattr(ProcessProtocol, 'connection_lost', closed)
        settle = HostProcessRunner._settle
        async def record_settle(self, *args, **kwargs):
            cleanup_entered.set()
            return await settle(self, *args, **kwargs)
        monkeypatch.setattr(HostProcessRunner, '_settle', record_settle)
        async def consume(stream, data):
            entered.set()
            await release.wait()
        task = asyncio.create_task(HostProcessRunner(ProcessSettings(cleanup_timeout_seconds=0.1)).run(
            request(tmp_path, "print('ready')"), consume))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(eof.wait(), 5)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(cleanup_entered.wait(), 0.3)
            assert not task.done()
            release.set()
            outcome = await task
            assert outcome.termination == 'exited' and outcome.consumer_settled
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_cancel_during_spawn_takes_custody_of_eventual_handle(monkeypatch, tmp_path):
    async def scenario():
        loop = asyncio.get_running_loop()
        real_spawn = loop.subprocess_exec
        created, release = asyncio.Event(), asyncio.Event()
        handles = []
        terminations = []
        terminate = HostProcessRunner._terminate
        async def terminate_once(transport):
            terminations.append(transport.get_pid())
            assert len(terminations) == 1, 'Each owned group is terminated only once'
            await terminate(transport)
        monkeypatch.setattr(HostProcessRunner, '_terminate', staticmethod(terminate_once))
        async def delayed(*args, **kwargs):
            result = await real_spawn(*args, **kwargs)
            handles.append(result[0])
            created.set()
            await release.wait()
            return result
        monkeypatch.setattr(loop, 'subprocess_exec', delayed)
        async def consume(stream, data):
            pass
        task = asyncio.create_task(HostProcessRunner().run(request(tmp_path,
            'import time;time.sleep(60)'), consume))
        await asyncio.wait_for(created.wait(), 5)
        pid = handles[0].get_pid()
        task.cancel('during-spawn')
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ('during-spawn',)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    asyncio.run(scenario())


def test_protocol_high_water_pauses_both_pipes_until_consumer_settles():
    async def scenario():
        operations, chunks = [], []
        entered, release = asyncio.Event(), asyncio.Event()
        protocol = ProcessProtocol(4)
        pipes = {fd: SimpleNamespace(pause_reading=lambda fd=fd: operations.append(('pause', fd)),
            resume_reading=lambda fd=fd: operations.append(('resume', fd))) for fd in (1, 2)}
        protocol.connection_made(SimpleNamespace(get_pipe_transport=pipes.__getitem__))
        async def consume(stream, data):
            entered.set()
            await release.wait()
            chunks.append((stream, data))
        protocol.pipe_data_received(1, b'abcdef')
        delivery = asyncio.create_task(protocol.deliver(consume))
        await asyncio.wait_for(entered.wait(), 5)
        assert protocol.pending_bytes == 6 and operations == [('pause', 1), ('pause', 2)]
        release.set()
        protocol.pipe_connection_lost(1, None)
        protocol.pipe_connection_lost(2, None)
        await delivery
        assert chunks == [('stdout', b'abcdef')] and protocol.pending_bytes == 0
        assert protocol.output_complete
    asyncio.run(scenario())


def test_actual_spawn_timeout_error_keeps_exception_identity(monkeypatch, tmp_path):
    async def scenario():
        failure = TimeoutError('spawn failed')
        async def fail(*args, **kwargs):
            raise failure
        monkeypatch.setattr(asyncio.get_running_loop(), 'subprocess_exec', fail)
        async def consume(stream, data):
            pytest.fail('No child was acquired')
        with pytest.raises(TimeoutError) as caught:
            await HostProcessRunner().run(request(tmp_path, "print('unused')"), consume)
        assert caught.value is failure
    asyncio.run(scenario())


def test_pipe_close_failure_stays_mechanism_failure(monkeypatch, tmp_path):
    async def scenario():
        failure = OSError('close failed')
        protocols, chunks = [], []
        close = ProcessProtocol.close_pipes
        def fail(self):
            protocols.append(self)
            raise failure
        monkeypatch.setattr(ProcessProtocol, 'close_pipes', fail)
        async def consume(stream, data):
            chunks.append(data)
        script = ("import subprocess,sys;"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            "print(p.pid,flush=True)")
        try:
            with pytest.raises(ProcessCleanupError) as caught:
                await HostProcessRunner().run(request(tmp_path, script, eof_policy='shell'), consume)
            assert caught.value.__cause__ is failure
            assert not caught.value.facts['pipes_closed']
        finally:
            if chunks:
                os.kill(int(b''.join(chunks)), 9)
            for protocol in protocols:
                close(protocol)
            if 'caught' in locals() and caught.value.consumer_task is not None:
                await caught.value.consumer_task
    asyncio.run(scenario())


def test_backpressure_callback_fault_reaches_runner_instead_of_event_loop():
    async def scenario():
        failure = RuntimeError('pause failed')
        def fail():
            raise failure
        protocol = ProcessProtocol(1)
        protocol.connection_made(SimpleNamespace(get_pipe_transport=lambda fd: SimpleNamespace(pause_reading=fail)))
        protocol.pipe_data_received(1, b'byte')
        assert protocol.errors == [failure] and protocol.changed.is_set()
    asyncio.run(scenario())


def test_consumer_timeout_error_is_not_a_process_timeout(tmp_path):
    async def scenario():
        failure = TimeoutError('archive operation')
        async def consume(stream, data):
            raise failure
        with pytest.raises(TimeoutError) as caught:
            await HostProcessRunner().run(request(tmp_path, "print('data')"), consume)
        assert caught.value is failure
    asyncio.run(scenario())


def test_unsettled_consumer_hits_one_cleanup_budget_and_keeps_cancel_cause(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def consume(stream, data):
            entered.set()
            await release.wait()
        task = asyncio.create_task(HostProcessRunner(ProcessSettings(cleanup_timeout_seconds=0.1)).run(
            request(tmp_path, "import time;print('ready',flush=True);time.sleep(60)"), consume))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel('primary-cancel')
            with pytest.raises(ProcessCleanupError) as caught:
                await asyncio.wait_for(task, 5)
            error = caught.value
            assert error.facts['process_reaped'] and error.facts['pipes_closed']
            assert not error.facts['consumer_settled']
            assert isinstance(error.__cause__, asyncio.CancelledError)
            assert error.__cause__.args == ('primary-cancel',)
            assert not error.consumer_task.done()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if task.done() and isinstance(task.exception(), ProcessCleanupError):
                await task.exception().consumer_task
    asyncio.run(scenario())


def test_merge_output_is_requested_at_spawn_and_streams_to_one_consumer(tmp_path):
    async def scenario():
        chunks = []
        async def consume(stream, data):
            assert stream == 'stdout'
            chunks.append(data)
        outcome = await HostProcessRunner().run(request(tmp_path,
            "import os;os.write(1,b'O');os.write(2,b'E')", merge_output=True), consume)
        assert outcome.output_complete and b''.join(chunks) == b'OE'
    asyncio.run(scenario())


def test_stop_before_spawn_never_invokes_public_spawn(monkeypatch, tmp_path):
    async def scenario():
        async def forbidden(*args, **kwargs):
            pytest.fail('A stopped run must not start another child')
        monkeypatch.setattr(asyncio.get_running_loop(), 'subprocess_exec', forbidden)
        async def consume(stream, data):
            pytest.fail('No output exists before start')
        result = await HostProcessRunner().run(request(tmp_path, "print('no')"), consume,
                                               SimpleNamespace(requested=True))
        assert result.termination == 'user_stop' and result.returncode is None
    asyncio.run(scenario())


@pytest.mark.parametrize('returncode,parent_code,raises', [(0, None, False), (1, 0, False), (1, None, True)])
def test_windows_taskkill_checks_result_and_closes_helper(monkeypatch, returncode, parent_code, raises):
    async def scenario():
        seen, closed = [], []
        async def spawn(factory, *argv, **kwargs):
            seen.append((argv, kwargs))
            return SimpleNamespace(get_returncode=lambda: returncode, close=lambda: closed.append(True)), factory()
        monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt'))
        monkeypatch.setattr(module.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
        monkeypatch.setattr(asyncio.get_running_loop(), 'subprocess_exec', spawn)
        parent = SimpleNamespace(get_pid=lambda: 1234, get_returncode=lambda: parent_code)
        if raises:
            with pytest.raises(OSError, match='termination failed'):
                await HostProcessRunner._terminate(parent)
        else:
            await HostProcessRunner._terminate(parent)
        assert seen[0][0] == ('taskkill', '/PID', '1234', '/T', '/F')
        assert seen[0][1]['stdout'] == seen[0][1]['stderr'] == module.subprocess.DEVNULL
        assert seen[0][1]['creationflags'] == 0x08000000
        assert closed == [True]
    asyncio.run(scenario())
