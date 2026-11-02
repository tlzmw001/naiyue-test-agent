"""Live observations cannot settle owner commands or replace confirmed history."""
import asyncio

import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from lifecycle_helpers import config
from observation_helpers import running,item,frame


def test_live_snapshot_dedupe_and_foreign_run_are_scoped(tmp_path):
    async def run(r):
        b,c,cmd=await running(r)
        records=[item(cmd,'message_delta',dict(message_id='a',text='hello',index=1,end_index=1,reply_id=None))]
        packet=frame(b,c,records)
        await r.control.receive(c,packet);await r.control.receive(c,packet)
        snapshot=await r.observations.snapshot(b.task_id)
        assert snapshot['live']['messages'][0]['content']=='hello'
        assert snapshot['live']['messages'][0]['provisional'] is True
        assert snapshot['archive']['messages']==[] and r.history_page(b.task_id)['last_seq']==0
        assert r.registry.get_command(cmd['command_id']).status=='running'
        foreign=records[0]|{'run_id':'f'*32}
        with pytest.raises(CloudError):await r.control.receive(c,frame(b,c,[foreign],seq=2))
        await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))


def test_subscription_snapshot_then_replay_has_no_gap_and_detects_slow_reader(tmp_path):
    async def run(r):
        b,c,cmd=await running(r)
        lease=await r.observations.subscribe(b.task_id)
        first=await lease.next();assert first['event']=='snapshot'
        for n in range(1,5):
            await r.control.receive(c,frame(b,c,[item(cmd,'message_delta',dict(message_id='a',text=str(n),
                index=n,end_index=n,reply_id=None))],seq=n))
        event=await lease.next();assert event['event']=='resync'
        latest=await lease.next();assert latest['event']=='snapshot'
        assert latest['data']['live']['messages'][0]['content']=='1234'
        cursor=latest['id'];await lease.aclose()
        resumed=await r.observations.subscribe(b.task_id,last_event_id=cursor)
        await r.control.receive(c,frame(b,c,[item(cmd,'message_delta',dict(message_id='a',text='5',index=5,end_index=5,reply_id=None))],seq=5))
        assert (await resumed.next())['event']=='update'
        with pytest.raises(CloudError):await r.observations.subscribe(b.task_id,last_event_id=cursor+'bad')
        await resumed.aclose();await r.observations.aclose();await r.control.aclose()
        assert not r.observations.leases
    from naiyue_test_agent.contracts.cloud_observation import ObservationLimits
    with open_cloud_runtime(config(tmp_path).model_copy(update={'observation_limits':ObservationLimits(replay_events=2)})) as r:
        asyncio.run(run(r))


def test_snapshot_retries_when_an_observation_arrives_during_archive_read(tmp_path,monkeypatch):
    async def run(r):
        b,c,cmd=await running(r);entered=asyncio.Event();release=asyncio.Event();calls=0
        original=r.observations._read_snapshot
        async def read(*args,**kwargs):
            nonlocal calls
            value=await original(*args,**kwargs);calls+=1
            if calls==1:entered.set();await release.wait()
            return value
        monkeypatch.setattr(r.observations,'_read_snapshot',read)
        job=asyncio.create_task(r.observations.snapshot(b.task_id));await asyncio.wait_for(entered.wait(),5)
        await r.control.receive(c,frame(b,c,[item(cmd,'message_delta',dict(message_id='a',text='late',index=1,end_index=1,reply_id=None))]))
        release.set();snapshot=await job
        assert calls==2 and snapshot['live']['seq']==1 and snapshot['live']['messages'][0]['content']=='late'
        lease=await r.observations.subscribe(b.task_id,last_event_id=snapshot['cursor'])
        await r.control.receive(c,frame(b,c,[item(cmd,'message_delta',dict(message_id='a',text=' next',index=2,end_index=2,reply_id=None))],seq=2))
        event=await lease.next();assert event['event']=='update' and event['data']['messages'][0]['content']=='late next'
        await lease.aclose();await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))


def test_observation_gap_invalidates_old_cursor_and_does_not_invent_text(tmp_path):
    async def run(r):
        b,c,cmd=await running(r)
        await r.control.receive(c,frame(b,c,[item(cmd,'message_delta',dict(message_id='a',text='first',index=1,end_index=1,reply_id=None))]))
        old=await r.observations.snapshot(b.task_id)
        await r.control.receive(c,frame(b,c,[item(cmd,'message_delta',dict(message_id='a',text='third',index=3,end_index=3,reply_id=None))],seq=3))
        snapshot=await r.observations.snapshot(b.task_id)
        assert snapshot['live']['capture_incomplete'] and snapshot['live']['messages']==[]
        lease=await r.observations.subscribe(b.task_id,last_event_id=old['cursor'])
        assert (await lease.next())['event']=='resync' and (await lease.next())['event']=='snapshot'
        await lease.aclose();await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))
