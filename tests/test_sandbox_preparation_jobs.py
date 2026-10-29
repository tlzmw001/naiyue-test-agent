import asyncio
from threading import Event

import pytest

from naiyue_test_agent.application.environments.preparation import SandboxPreparations
from naiyue_test_agent.contracts.sandbox import SandboxError


def test_single_flight_cancellation_and_other_binding_isolation():
    async def scenario():
        jobs=SandboxPreparations(lambda:2)
        entered,release=asyncio.Event(),asyncio.Event()
        calls=[]
        async def prepare(progress):
            calls.append('first');progress.update('snapshot');entered.set();await release.wait()
            progress.update('container');return 'done'
        first=jobs.start('A','P','S',prepare)
        assert jobs.start('A','P','S',prepare) is first
        await entered.wait()
        assert jobs.view('A')['phase']=='snapshot'
        async def other(progress):progress.update('upload',completed=3,total=10);return 'other'
        second=jobs.start('B','P','T',other)
        assert await second=='other'
        stopping=asyncio.create_task(jobs.stop('A'))
        await asyncio.sleep(0)
        assert jobs.view('A')['phase']=='stopping'
        release.set();await stopping
        assert first.exception().code=='env_preparation_cancelled'
        assert jobs.view('A')['state']=='cancelled'
        assert calls==['first'] and jobs.view('B')['state']=='ready'
        await jobs.close()
    asyncio.run(scenario())


def test_capacity_and_worker_checkpoint_acknowledged_progress():
    async def scenario():
        jobs=SandboxPreparations(lambda:1)
        entered,release=Event(),Event()
        async def prepare(progress):
            def work():
                progress.update('upload',completed=5,total=10)
                entered.set();assert release.wait(10);progress.check()
            from naiyue_test_agent.agent_runtime.async_utils import run_blocking
            await run_blocking(work)
        task=jobs.start('A','P','S',prepare)
        assert await asyncio.to_thread(entered.wait,10)
        with pytest.raises(SandboxError,match='env_retained_capacity'):jobs.start('B','P','T',prepare)
        assert jobs.view('A')['completed']==5
        closing=asyncio.create_task(jobs.close());await asyncio.sleep(0)
        assert not closing.done()
        release.set();await closing
        assert task.exception().code=='env_preparation_cancelled'
        with pytest.raises(SandboxError):jobs.start('B','P','T',prepare)
    asyncio.run(scenario())
