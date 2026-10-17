"""Real owned subprocesses, bounded streaming, and cancellation settlement."""

import asyncio
import os
import signal
import sys
from types import SimpleNamespace

import pytest

from naiyue_test_agent.tools.processes import (
    HostProcessRunner, ProcessCleanupError, ProcessRequest, run_async_sync,
)
from naiyue_test_agent.tools.settings import ProcessSettings


def request(root, script, **kwargs):
    return ProcessRequest(argv=[sys.executable, '-u', '-c', script], cwd=root,
        env=dict(os.environ), timeout_seconds=5, execution_environment={'kind': 'host'},
        **kwargs)


@pytest.mark.parametrize('code', [0, 7])
def test_streams_and_exit_are_separate_facts(tmp_path, code):
    chunks = {'stdout': bytearray(), 'stderr': bytearray()}
    async def consume(stream, data):
        chunks[stream].extend(data)
    result = run_async_sync(lambda: HostProcessRunner().run(request(tmp_path,
        f"import os,sys;os.write(1,b'\\xe4');os.write(1,b'\\xb8\\xad\\xff');"
        f"os.write(2,b'error');sys.exit({code})"), consume))
    assert result.returncode == code and result.termination == 'exited'
    assert result.output_complete and result.process_reaped and result.pipes_closed
    assert chunks == {'stdout': b'\xe4\xb8\xad\xff', 'stderr': b'error'}


def test_dual_stream_backpressure_does_not_lose_bytes(tmp_path):
    async def scenario():
        chunks = {'stdout': bytearray(), 'stderr': bytearray()}
        async def consume(stream, data):
            await asyncio.sleep(0)
            chunks[stream].extend(data)
        runner = HostProcessRunner(ProcessSettings(max_pending_output_bytes=4096))
        outcome = await runner.run(request(tmp_path,
            "import os;[(os.write(1,b'O'*8192),os.write(2,b'E'*8192)) for _ in range(64)]"), consume)
        assert chunks == {'stdout': b'O' * 524288, 'stderr': b'E' * 524288}
        assert outcome.output_complete and outcome.backpressure_count > 0
    asyncio.run(scenario())


def test_timeout_reaps_child_and_keeps_received_output(tmp_path):
    async def scenario():
        chunks = []
        async def consume(stream, data):
            chunks.append(data)
        req = request(tmp_path, "import os,time;print(os.getpid(),flush=True);time.sleep(60)")
        req = ProcessRequest(**{**vars(req), 'timeout_seconds': 0.3})
        outcome = await HostProcessRunner().run(req, consume)
        assert outcome.termination == 'timeout' and outcome.process_reaped and outcome.pipes_closed
        pid = int(b''.join(chunks))
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    asyncio.run(scenario())


def test_stop_signal_stops_started_process(tmp_path):
    async def scenario():
        control = SimpleNamespace(requested=False)
        async def consume(stream, data):
            control.requested = True
        outcome = await HostProcessRunner().run(request(tmp_path,
            "import time;print('started',flush=True);time.sleep(60)"), consume, control)
        assert outcome.termination == 'user_stop' and outcome.process_reaped
    asyncio.run(scenario())


def test_cancel_keeps_signal_after_reaping(tmp_path):
    async def scenario():
        started = asyncio.Event()
        pids = []
        async def consume(stream, data):
            pids.append(int(data))
            started.set()
        task = asyncio.create_task(HostProcessRunner().run(request(tmp_path,
            "import os,time;print(os.getpid(),flush=True);time.sleep(60)"), consume))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel('original-cancellation')
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ('original-cancellation',)
        with pytest.raises(ProcessLookupError):
            os.kill(pids[0], 0)
    asyncio.run(scenario())


def test_consumer_fault_propagates_after_reaping(tmp_path):
    async def scenario():
        failure = RuntimeError('consumer failed')
        pids = []
        async def consume(stream, data):
            pids.append(int(data))
            raise failure
        with pytest.raises(RuntimeError) as caught:
            await HostProcessRunner().run(request(tmp_path,
                "import os,time;print(os.getpid(),flush=True);time.sleep(60)"), consume)
        assert caught.value is failure
        with pytest.raises(ProcessLookupError):
            os.kill(pids[0], 0)
    asyncio.run(scenario())


def test_sync_bridge_inside_existing_loop_uses_same_runner(tmp_path):
    async def scenario():
        chunks = []
        async def consume(stream, data):
            chunks.append(data)
        result = run_async_sync(lambda: HostProcessRunner().run(request(tmp_path, "print('sync')"), consume))
        assert result.output_complete and b''.join(chunks) == b'sync\n'
    asyncio.run(scenario())


@pytest.mark.parametrize('policy,termination', [('shell', 'exited'), ('complete', 'timeout')])
def test_parent_exit_with_descendant_pipe_requires_explicit_incomplete(tmp_path, policy, termination):
    async def scenario():
        chunks = []
        async def consume(stream, data):
            chunks.append(data)
        child_script = "import time;time.sleep(60)"
        script = ("import subprocess,sys;"
            f"p=subprocess.Popen([sys.executable,'-c',{child_script!r}]);"
            "print(p.pid,flush=True)")
        req = request(tmp_path, script, eof_policy=policy)
        req = ProcessRequest(**{**vars(req), 'timeout_seconds': 0.4})
        try:
            outcome = await HostProcessRunner().run(req, consume)
            assert outcome.termination == termination
            assert not outcome.output_complete and outcome.pipes_closed
            assert outcome.process_reaped
        finally:
            if chunks:
                os.kill(int(b''.join(chunks)), signal.SIGKILL)
    asyncio.run(scenario())
