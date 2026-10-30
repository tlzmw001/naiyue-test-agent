"""Cleanup is a finite batch and may be explicitly retried after new evidence."""
import asyncio
from types import SimpleNamespace
import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError
from test_sandbox_connections import row,Channel


def test_failed_cleanup_is_not_repeated_by_get_but_explicit_recovery_can_retry():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def scenario():
        closed=[]
        class Retiring(Channel):
            async def close(self,*,deadline=None):
                closed.append(1)
                if len(closed)==1:raise SandboxError('env_connection_cleanup_unconfirmed')
        async def factory(record,lane):return Retiring()
        pool=SandboxConnections(factory,max_bindings=2,max_connecting=2)
        old=await pool.get(row('a'));other=await pool.get(row('b'))
        with pytest.raises(SandboxError,match='env_connection_cleanup_unconfirmed'):await pool.invalidate('a')
        for _ in range(3):
            with pytest.raises(SandboxError):await pool.get(row('a'))
        assert len(closed)==1 and await pool.get(row('b')) is other
        await pool.invalidate('a')
        assert len(closed)==2
        assert await pool.get(row('a')) is not old
        await pool.close()
    asyncio.run(scenario())


def test_one_cleanup_deadline_is_shared_by_waiters_and_lanes():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event();closes=[]
        class Blocked(Channel):
            async def close(self,*,deadline=None):closes.append(self);entered.set();await release.wait()
        async def factory(record,lane):return Blocked()
        pool=SandboxConnections(factory,max_bindings=1,max_connecting=2,cleanup_timeout=.1)
        await pool.get(row('a'));await pool.get(row('a'),lane='bulk')
        one=asyncio.create_task(pool.invalidate('a'));await entered.wait()
        two=asyncio.create_task(pool.invalidate('a'))
        results=await asyncio.gather(one,two,return_exceptions=True)
        assert all(isinstance(error,SandboxError) and error.code=='env_connection_cleanup_unconfirmed' for error in results)
        assert len(closes)==2
        with pytest.raises(SandboxError):await pool.get(row('a'))
        release.set();await pool.invalidate('a');await pool.close()
    asyncio.run(scenario())


def test_child_cleanup_has_two_attempts_and_no_unbounded_helper_wait(monkeypatch):
    from naiyue_test_agent.execution import sandbox_child as module
    async def scenario():
        calls=[]
        async def wait():await asyncio.Event().wait()
        process=SimpleNamespace(stdin=None,returncode=None,wait=wait)
        async def terminate(process):calls.append(1);raise OSError('fixture unavailable')
        monkeypatch.setattr(module,'terminate_tree',terminate)
        with pytest.raises(SandboxError,match='env_connection_cleanup_unconfirmed'):
            await module.close_child(process,timeout=.2,attempts=2)
        assert calls==[1,1]
    asyncio.run(scenario())


def test_windows_killer_nonzero_is_success_when_owned_process_has_exited(monkeypatch):
    from naiyue_test_agent.execution import sandbox_child as module
    async def scenario():
        process=SimpleNamespace(pid=1234,returncode=None,_native_owner=object())
        async def wait():process.returncode=0;return 128
        killer=SimpleNamespace(returncode=128,wait=wait)
        async def spawn(*args,**kwargs):return killer
        monkeypatch.setattr(module,'os',SimpleNamespace(name='nt'))
        monkeypatch.setattr(module,'subprocess',SimpleNamespace(DEVNULL=-3,CREATE_NO_WINDOW=0))
        monkeypatch.setattr(module.asyncio,'create_subprocess_exec',spawn)
        await module.terminate_tree(process)
        assert process.returncode==0
    asyncio.run(scenario())


def test_exited_posix_leader_does_not_leave_descendant_holding_pipes():
    import os
    import signal
    import sys
    from naiyue_test_agent.execution.sandbox_child import spawn_child,close_child,drain
    if os.name=='nt':return  # POSIX fork/process-group contract; Windows has separate native acceptance.
    async def scenario():
        process=await spawn_child([sys.executable,'-u','-c',
            'import os,time\nif os.fork()==0:\n print(os.getpid(),flush=True)\n time.sleep(30)\nelse: os._exit(0)'],
            env=None,cwd=None,limit=65536)
        readers=[]
        try:
            async with asyncio.timeout(5):
                assert int(await process.stdout.readline())>0
                while process.returncode is None:await asyncio.sleep(0)
            readers=[asyncio.create_task(drain(stream)) for stream in (process.stdout,process.stderr)]
            await close_child(process,timeout=1,readers=readers)
            assert process.returncode==0 and all(task.done() for task in readers)
        finally:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            for task in readers:task.cancel()
            await asyncio.gather(*readers,return_exceptions=True)
    asyncio.run(scenario())


def test_cancelled_spawn_retains_original_native_owner_before_cleanup(monkeypatch):
    from naiyue_test_agent.execution import sandbox_child as module
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event();owner=object();observed=[]
        async def read(_):return b''
        process=SimpleNamespace(stdout=SimpleNamespace(read=read),stderr=SimpleNamespace(read=read),
            _transport=SimpleNamespace(get_extra_info=lambda _:owner))
        async def create(*_,**__):entered.set();await release.wait();return process
        async def close(owned,**kwargs):observed.append(getattr(owned,'_native_owner',None))
        monkeypatch.setattr(module.asyncio,'create_subprocess_exec',create)
        monkeypatch.setattr(module,'close_child',close)
        task=asyncio.create_task(module.spawn_child(['fixture'],env=None,cwd=None,limit=65536))
        await entered.wait();task.cancel();release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert observed==[owner]
    asyncio.run(scenario())
