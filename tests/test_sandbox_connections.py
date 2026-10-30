"""Connection ownership is per immutable sandbox, never per SSH host alias."""
import asyncio
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError


def row(binding, *, container='container', host='jump'):
    return SimpleNamespace(binding_id=binding,container_id=container,session_id='session-'+binding,
        daemon_id='daemon',settings={'ssh':{'host':host}})


class Channel:
    failed=None
    def __init__(self):self.closed=0
    async def close(self,*,deadline=None):self.closed+=1


def test_same_binding_connects_once_and_other_sandbox_is_independent():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def run():
        entered=asyncio.Event();release=asyncio.Event();created=[]
        async def factory(record,lane):
            created.append((record.binding_id,lane));entered.set()
            if record.binding_id=='a':await release.wait()
            return Channel()
        pool=SandboxConnections(factory,max_bindings=4,max_connecting=2)
        first=asyncio.create_task(pool.get(row('a')))
        await entered.wait()
        second=asyncio.create_task(pool.get(row('a')))
        other=await pool.get(row('b'))
        release.set()
        a,b=await asyncio.gather(first,second)
        assert a is b and a is not other and created==[('a','main'),('b','main')]
        await pool.invalidate('a')
        assert a.closed==1 and other.closed==0
        assert await pool.get(row('b')) is other
        await pool.close()
        assert other.closed==1
    asyncio.run(run())


def test_cancelled_waiter_does_not_cancel_shared_open_or_leak_shutdown():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def run():
        entered=asyncio.Event();release=asyncio.Event();channel=Channel()
        async def factory(record,lane):
            entered.set();await release.wait();return channel
        pool=SandboxConnections(factory,max_bindings=2,max_connecting=2)
        first=asyncio.create_task(pool.get(row('a')));await entered.wait()
        second=asyncio.create_task(pool.get(row('a')))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        release.set()
        assert await second is channel
        await pool.close()
        assert channel.closed==1
        with pytest.raises(SandboxError,match='env_stopped'):await pool.get(row('b'))
    asyncio.run(run())


def test_identity_change_closes_old_channel_and_capacity_is_bounded():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def run():
        async def factory(record,lane):return Channel()
        pool=SandboxConnections(factory,max_bindings=1,max_connecting=1)
        old=await pool.get(row('a'))
        new=await pool.get(row('a',container='replacement'))
        assert old.closed==1 and new is not old
        with pytest.raises(SandboxError,match='env_connection_capacity'):await pool.get(row('b'))
        await pool.close()
    asyncio.run(run())


def test_failed_open_can_be_retried_without_affecting_another_binding():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def run():
        calls=[]
        async def factory(record,lane):
            calls.append(record.binding_id)
            if calls==['b','a']:raise SandboxError('env_unreachable')
            return Channel()
        pool=SandboxConnections(factory,max_bindings=3,max_connecting=2)
        b=await pool.get(row('b'))
        with pytest.raises(SandboxError,match='env_unreachable'):await pool.get(row('a'))
        a=await pool.get(row('a'))
        assert a is not b and b.closed==0 and calls==['b','a','a']
        await pool.close()
    asyncio.run(run())


def test_invalidate_during_open_never_publishes_stale_connection():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def run():
        entered=asyncio.Event();release=asyncio.Event();channel=Channel()
        async def factory(record,lane):
            entered.set();await release.wait();return channel
        pool=SandboxConnections(factory,max_bindings=2,max_connecting=2)
        opening=asyncio.create_task(pool.get(row('a')));await entered.wait()
        invalidating=asyncio.create_task(pool.invalidate('a'))
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(SandboxError,match='env_stopped'):await opening
        await invalidating
        assert channel.closed==1
        await pool.close()
    asyncio.run(run())


def test_invalidate_propagates_cleanup_uncertainty_during_acquisition():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        class Unconfirmed(Channel):
            async def close(self,*,deadline=None):raise SandboxError('env_connection_cleanup_unconfirmed')
        async def factory(record,lane):entered.set();await release.wait();return Unconfirmed()
        pool=SandboxConnections(factory,max_bindings=1,max_connecting=1)
        opening=asyncio.create_task(pool.get(row('a')));await entered.wait()
        stopping=asyncio.create_task(pool.invalidate('a'));await asyncio.sleep(0);release.set()
        with pytest.raises(SandboxError):await opening
        with pytest.raises(SandboxError,match='env_connection_cleanup_unconfirmed'):await stopping
        with pytest.raises(SandboxError):await pool.close()
    asyncio.run(run())


def test_invalidation_blocks_new_bulk_lane_until_retirement_finishes():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        class SlowClose(Channel):
            async def close(self,*,deadline=None):entered.set();await release.wait();self.closed+=1
        async def factory(record,lane):return SlowClose()
        pool=SandboxConnections(factory,max_bindings=1,max_connecting=1)
        await pool.get(row('a'))
        invalidation=asyncio.create_task(pool.invalidate('a'));await entered.wait()
        with pytest.raises(SandboxError,match='env_stopped'):await pool.get(row('a'),lane='bulk')
        release.set();await invalidation;await pool.close()
        assert pool._entries=={}
    asyncio.run(run())


def test_nonblocking_invalidation_seals_all_lanes_without_waiting_for_open():
    from naiyue_test_agent.execution.sandbox_connections import SandboxConnections
    async def run():
        entered=asyncio.Event();release=asyncio.Event();channel=Channel()
        async def factory(record,lane):entered.set();await release.wait();return channel
        pool=SandboxConnections(factory,max_bindings=1,max_connecting=1)
        opening=asyncio.create_task(pool.get(row('a')));await entered.wait()
        await pool.invalidate('a',wait=False)
        assert not opening.done()
        with pytest.raises(SandboxError,match='env_stopped'):await pool.get(row('a'),lane='bulk')
        release.set()
        with pytest.raises(SandboxError):await opening
        await pool.close();assert channel.closed==1
    asyncio.run(run())
