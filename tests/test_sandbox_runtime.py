"""One event-loop owner serves sync callers and multiple asynchronous sessions."""
import asyncio
import threading

from naiyue_test_agent.contracts.sandbox import SandboxError
import pytest


def test_single_runtime_loop_is_reused_across_sync_and_async_callers():
    from naiyue_test_agent.execution.sandbox_runtime import SandboxRuntime
    runtime=SandboxRuntime()
    async def identity():return id(asyncio.get_running_loop()),threading.get_ident()
    first=runtime.call(identity())
    async def run():
        return await asyncio.gather(*(runtime.await_call(identity()) for _ in range(12)))
    assert asyncio.run(run())==[first]*12
    assert runtime.call(identity())==first and first[1]!=threading.get_ident()
    runtime.close()
    assert not runtime.thread.is_alive()
    with pytest.raises(SandboxError,match='env_stopped'):runtime.call(identity())


def test_async_wait_cancellation_reaches_only_its_coroutine():
    from naiyue_test_agent.execution.sandbox_runtime import SandboxRuntime
    runtime=SandboxRuntime()
    started=threading.Event();stopped=threading.Event()
    async def wait():
        started.set()
        try:await asyncio.Event().wait()
        finally:stopped.set()
    async def run():
        task=asyncio.create_task(runtime.await_call(wait()))
        await asyncio.to_thread(started.wait)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run())
    runtime.close()
    assert stopped.is_set() and not runtime.thread.is_alive()
