"""An SSE subscriber owns a cursor, not a private unbounded queue or DB transaction."""
import asyncio
from copy import deepcopy

from naiyue_test_agent.contracts.cloud_events import canonical_json


def encode_sse(event):
    value=('id: '+event['id']+'\n' if 'id' in event else '')+'event: '+event['event']+'\n'
    return (value+'data: '+canonical_json(event['data'])+'\n\n').encode()


class ObservationLease:
    def __init__(self,service,task_id,thread_id,state,snapshot,selected):
        self.service,self.task_id,self.thread_id,self.state=service,task_id,thread_id,state
        self.closed=False;self.position=snapshot['live']['seq'];self.pending=[]
        self.deleting=False
        self.epoch=state.epoch
        self.deadline=asyncio.get_running_loop().time()+service.limits.stream_seconds
        if selected and selected['epoch']==state.epoch and selected['seq']<=state.seq and (
                not state.ring or selected['seq']>=state.ring[0][0]['seq']-1):
            self.position=selected['seq']
        else:
            if selected:self.pending.append(dict(event='resync',data={'reason':'cursor_unavailable'}))
            self.pending.append(dict(event='snapshot',data=snapshot,id=snapshot['cursor']))

    async def _resync(self,reason):
        from naiyue_test_agent.contracts.cloud_tasks import CloudError
        try:snapshot=await self.service.snapshot(self.task_id,thread_id=self.thread_id)
        except CloudError:
            if not self.deleting:raise
            self.deleting=False;await self.aclose()
            return dict(event='task_deleting',data={'task_id':self.task_id})
        self.position=snapshot['live']['seq'];self.epoch=self.state.epoch
        self.pending.append(dict(event='snapshot',data=snapshot,id=snapshot['cursor']))
        return dict(event='resync',data={'reason':reason})

    async def next(self):
        if self.deleting:
            self.deleting=False
            await self.aclose()
            return dict(event='task_deleting',data={'task_id':self.task_id})
        while not self.closed and not self.service.closing:
            if self.deleting:
                self.deleting=False
                await self.aclose()
                return dict(event='task_deleting',data={'task_id':self.task_id})
            if asyncio.get_running_loop().time()>=self.deadline:await self.aclose();break
            if self.pending:return self.pending.pop(0)
            state=self.state
            if self.epoch!=state.epoch:return await self._resync('observation_epoch_changed')
            if state.ring and self.position<state.ring[0][0]['seq']-1:return await self._resync('replay_gap')
            item=next((r for r,_ in state.ring if r['seq']>self.position),None)
            if item:
                self.position=item['seq']
                if item['event'] in ('archive','task_changed','resync'):
                    return await self._resync(item['event'])
                value=deepcopy(item['data'])
                def selected(row):
                    return row.get('thread_id')==self.thread_id if self.thread_id else row.get('source') is None
                value['messages']=[m for m in value['messages'] if selected(m)]
                value['tools']=[m for m in value['tools'] if selected(m)]
                value['observed_runs']=[m for m in value['observed_runs'] if selected(m)]
                event=dict(event='update',data=value,id=self.service._cursor(self.task_id,self.thread_id,state,self.position))
                if len(encode_sse(event))>self.service.runtime.settings.max_frame_bytes:
                    return await self._resync('event_budget')
                return event
            state.wake.clear()
            remaining=self.deadline-asyncio.get_running_loop().time()
            try:await asyncio.wait_for(state.wake.wait(),min(remaining,self.service.limits.heartbeat_seconds))
            except TimeoutError:
                if asyncio.get_running_loop().time()>=self.deadline:await self.aclose();break
                return dict(event='heartbeat',data={})
        raise StopAsyncIteration

    async def chunks(self):
        try:
            while True:
                try:event=await self.next()
                except StopAsyncIteration:break
                yield encode_sse(event)
        finally:await self.aclose()

    async def aclose(self):
        self.closed=True;self.service.leases.discard(self);self.state.wake.set()
