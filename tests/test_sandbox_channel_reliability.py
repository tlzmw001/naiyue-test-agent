"""Timeout/cancellation cannot turn legitimate late replies into protocol failures."""
import asyncio
import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError
from test_sandbox_channel import open_channel


BODY="""held={}
 while item:=await frames.receive():
  ident,r=item
  if r['params'].get('hold'):
   held[ident]=r;continue
  if r['params'].get('release'):
   for old,value in list(held.items()):
    value.update(ok=True,result=value['params']);await frames.send(old,value)
   held.clear()
  r.update(ok=True,result=r['params']);await frames.send(ident,r)"""


async def sent(channel,count):
    async with asyncio.timeout(5):
        while channel.sent_count<count:await asyncio.sleep(0)


def test_unsent_queue_timeout_preserves_channel_and_unlimited_search():
    async def scenario():
        channel=await open_channel(BODY,io_slots=1,timeout=.2)
        search=asyncio.create_task(channel.request('find',{'hold':True,'timeout':None}))
        await sent(channel,1)
        try:
            with pytest.raises(SandboxError,match='env_request_timeout'):await channel.request('read',{})
            assert not channel.failed and channel.sent_count==1 and not search.done()
            await channel.request('transport_hello',{'release':True})
            assert await search=={'hold':True,'timeout':None}
            assert await channel.request('read',{'next':True})=={'next':True}
        finally:await channel.close();await asyncio.gather(search,return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('cancel',[False,True])
def test_timed_out_or_cancelled_request_is_retired_until_its_late_reply(cancel):
    async def scenario():
        channel=await open_channel(BODY,io_slots=1,timeout=.2)
        first=asyncio.create_task(channel.request('read',{'hold':True}))
        await sent(channel,1)
        try:
            if cancel:
                first.cancel()
                with pytest.raises(asyncio.CancelledError):await first
            else:
                with pytest.raises(SandboxError,match='env_request_timeout'):await first
            assert len(channel.retired)==1 and not channel.failed
            assert await channel.request('read',{'B':True})=={'B':True}
            await channel.request('transport_hello',{'release':True})
            assert not channel.retired and not channel.failed
            assert await channel.request('read',{'C':True})=={'C':True}
        finally:await channel.close()
    asyncio.run(scenario())


def test_retired_capacity_is_not_evicted_to_admit_more_requests():
    async def scenario():
        channel=await open_channel(BODY,io_slots=1,control_slots=1,max_waiting=2,timeout=.2)
        try:
            for _ in range(2):
                with pytest.raises(SandboxError,match='env_request_timeout'):await channel.request('read',{'hold':True})
            before=channel.sent_count
            with pytest.raises(SandboxError,match='env_connection_capacity'):await channel.request('read',{})
            assert channel.sent_count==before and len(channel.retired)==2 and not channel.failed
            await channel.request('transport_hello',{'release':True})
            assert channel.retired=={}
            assert await channel.request('read',{'recovered':True})=={'recovered':True}
        finally:await channel.close()
    asyncio.run(scenario())


def test_third_control_request_queues_and_health_probe_has_independent_admission():
    async def scenario():
        channel=await open_channel(BODY,control_slots=2,timeout=2)
        tasks=[asyncio.create_task(channel.request('operation_stop',{'hold':True,'i':i})) for i in range(2)]
        await sent(channel,2)
        third=asyncio.create_task(channel.request('operation_stop',{'i':3}))
        try:
            await channel.request('transport_hello',{'release':True})
            assert await asyncio.gather(*tasks,third)==[{'hold':True,'i':0},{'hold':True,'i':1},{'i':3}]
            assert not channel.failed
        finally:await channel.close();await asyncio.gather(*tasks,third,return_exceptions=True)
    asyncio.run(scenario())


def test_request_preflight_preserves_surrogate_paths():
    async def scenario():
        channel=await open_channel(BODY)
        try:
            value={'path':'/workspace/\udcd6\udcd0'}
            assert await channel.request('read',value)==value
        finally:await channel.close()
    asyncio.run(scenario())
