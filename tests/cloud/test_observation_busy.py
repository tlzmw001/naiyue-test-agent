"""A moving snapshot is temporary observation contention, not stream failure."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from naiyue_test_agent.application.cloud.observation_stream import ObservationLease
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_stream_resync_retries_busy_snapshot_without_losing_position(caplog):
    async def run():
        wake=asyncio.Event();state=SimpleNamespace(epoch='e',seq=2,ring=[],wake=wake)
        snapshot={'live':{'seq':1},'cursor':'old'};fresh={'live':{'seq':2},'cursor':'new'}
        calls=[]
        async def read(*args,**kwargs):
            calls.append(1)
            if len(calls)==1:
                wake.set();raise CloudError('snapshot_busy',503)
            return fresh
        service=SimpleNamespace(limits=SimpleNamespace(stream_seconds=30,heartbeat_seconds=10),snapshot=read,leases=set(),closing=False)
        lease=ObservationLease(service,'task',None,state,snapshot,None);lease.pending.clear();service.leases.add(lease)
        result=await lease._resync('archive')
        assert result=={'event':'resync','data':{'reason':'archive'}}
        assert lease.position==2 and lease.pending[0]['data']==fresh and not lease.closed
        assert len(calls)==2 and 'cloud_snapshot_busy' in caplog.text
        await lease.aclose()
    asyncio.run(run())


def test_busy_resync_cancellation_closes_lease_and_other_errors_propagate():
    import pytest
    async def run():
        entered=asyncio.Event();state=SimpleNamespace(epoch='e',seq=2,ring=[],wake=asyncio.Event())
        async def busy(*args,**kwargs):
            entered.set();raise CloudError('snapshot_busy',503)
        service=SimpleNamespace(limits=SimpleNamespace(stream_seconds=30,heartbeat_seconds=10),snapshot=busy,leases=set(),closing=False)
        lease=ObservationLease(service,'task',None,state,{'live':{'seq':1},'cursor':'old'},None)
        lease.pending.clear();lease.epoch='previous';service.leases.add(lease)
        stream=lease.chunks();job=asyncio.create_task(anext(stream));await entered.wait();job.cancel()
        with pytest.raises(asyncio.CancelledError):await job
        assert lease.closed and not service.leases
        other=ObservationLease(service,'task',None,state,{'live':{'seq':1},'cursor':'old'},None)
        service.snapshot=AsyncMock(side_effect=CloudError('cloud_history_corrupt',503))
        with pytest.raises(CloudError,match='cloud_history_corrupt'):await other._resync('archive')
    asyncio.run(run())
