"""Bounded owner-loop presentation. None of these facts settle a task receipt."""
from collections import OrderedDict,deque
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from naiyue_test_agent.application.conversation.stream_projection import StreamingMessageProjection
from naiyue_test_agent.contracts.cloud_events import canonical_json
from naiyue_test_agent.contracts.cloud_tasks import CloudError


class LiveTask:
    def __init__(self,limits,connection_id=None):
        self.limits=limits;self.epoch=str(uuid4());self.seq=0;self.connection_id=connection_id
        self.generation=None
        self.runner_epoch=None;self.received=0;self.recent=deque(maxlen=128)
        self.projections=OrderedDict();self.sources={};self.steps={};self.tools=OrderedDict();self.runs=OrderedDict()
        self.incomplete=False;self.dropped=0;self.available=False;self.usage_incomplete=False
        self.ring=deque();self.ring_bytes=0;self.wake=None

    def reset(self,reason):
        self.projections.clear();self.sources.clear();self.steps.clear();self.tools.clear();self.runs.clear()
        self.epoch=str(uuid4());self.seq=0;self.ring.clear();self.ring_bytes=0
        self.runner_epoch=None;self.received=0;self.recent.clear()
        self.incomplete=True

    def accept(self,batch,fingerprint):
        end=batch.first_seq+len(batch.records)-1
        if self.runner_epoch==batch.epoch and end<=self.received:
            if (batch.first_seq,end,fingerprint) in self.recent:return False
            raise CloudError('observation_conflict')
        if (self.runner_epoch is not None and self.runner_epoch!=batch.epoch or batch.dropped
                or batch.first_seq!=(self.received+1 if self.runner_epoch==batch.epoch else 1)):
            self.reset('observation_gap')
        self.runner_epoch=batch.epoch;self.received=end;self.recent.append((batch.first_seq,end,fingerprint))
        self.dropped=min(9223372036854775807,self.dropped+batch.dropped);self.available=True
        return True

    def apply(self,batch):
        for record in batch.records:
            thread=record.thread_id
            if thread not in self.projections:
                if len(self.projections)>=self.limits.live_threads:
                    old,_=self.projections.popitem(last=False);self.sources.pop(old,None);self.steps.pop(old,None);self.incomplete=True
                self.projections[thread]=StreamingMessageProjection();self.steps[thread]={}
            self.sources[thread]=record.source.model_dump() if record.source else None
            projection=self.projections[thread];kind=record.event_type;d=record.data
            run_key=(record.run_id,thread)
            self.runs.setdefault(run_key,dict(run_id=record.run_id,thread_id=thread,source=self.sources[thread],status='running'))
            if kind in ('message_delta','message_reasoning_delta','message_completed','message_revised'):
                steps=self.steps[thread];steps[record.run_id]=steps.get(record.run_id,0)+1
                if kind in ('message_completed','message_revised'):
                    data=SimpleNamespace(snapshot_json=canonical_json(d.model_dump()),visibility='graph_values')
                else:data=SimpleNamespace(**d.model_dump())
                event=SimpleNamespace(event_type=kind,run_id=record.run_id,session_id=thread,
                    sequence=steps[record.run_id],source=None,data=data)
                try:projection(event)
                except ValueError:self.incomplete=True
                for row in projection.messages:
                    if row['id']==getattr(d,'message_id',getattr(d,'id',None)):
                        if kind in ('message_delta','message_reasoning_delta'):
                            row.pop('content_id',None)
                            row['reasoning_index' if kind=='message_reasoning_delta' else 'delta_index']=d.end_index
                        row.update(thread_id=thread,source=self.sources[thread],provisional=True,
                            state='draft' if kind in ('message_delta','message_reasoning_delta') else 'unconfirmed')
                    for field in ('content','reasoning'):
                        if isinstance(row.get(field),str) and len(row[field])>self.limits.text_chars:
                            row[field]=row[field][-self.limits.text_chars:];row[field+'_truncated']=True
                rows=projection.messages[-self.limits.live_messages:]
                run_ids={r['run_id'] for r in rows}
                projection.retain({r['id'] for r in rows},run_ids)
                self.steps[thread]={k:v for k,v in steps.items() if k in run_ids}
                if projection.incomplete:self.incomplete=True
            elif kind.startswith('tool_'):
                key=(record.run_id,thread,d.tool_call_id)
                self.tools[key]=dict(run_id=record.run_id,thread_id=thread,source=self.sources[thread],
                    event_type=kind,**d.model_dump())
                if 'text' in self.tools[key]:self.tools[key]['text']=self.tools[key]['text'][-self.limits.text_chars:]
                self.tools.move_to_end(key)
                while len(self.tools)>self.limits.live_tools:self.tools.popitem(last=False);self.incomplete=True
            elif kind=='run_finished':
                self.runs[run_key]=dict(run_id=record.run_id,thread_id=thread,source=self.sources[thread],status=d.status,error_type=d.error_type)
                for p in (self.projections.values() if record.source is None else (projection,)):
                    for row in p.messages:
                        if row.get('run_id')==record.run_id:row['state']='unconfirmed'
            while len(self.runs)>self.limits.live_messages:self.runs.popitem(last=False)
        # Bound aggregate memory as well as individual rows and thread counts.
        while len(canonical_json(self.view(all_threads=True)).encode())>self.limits.state_bytes and self.projections:
            old,_=self.projections.popitem(last=False);self.sources.pop(old,None);self.steps.pop(old,None);self.incomplete=True
        while len(canonical_json(self.view(all_threads=True)).encode())>self.limits.state_bytes and self.tools:
            self.tools.popitem(last=False);self.incomplete=True
        while len(canonical_json(self.view(all_threads=True)).encode())>self.limits.state_bytes and self.runs:
            self.runs.popitem(last=False);self.incomplete=True

    def identities(self):
        return [(thread,row['id']) for thread,p in self.projections.items() for row in p.messages]

    @staticmethod
    def confirmed(row,confirmation):
        if not confirmation:return False
        return row['content_id']==confirmation['content_id'] if row.get('content_id') else row.get('run_id')==confirmation['run_id']

    def discard_confirmed(self,confirmations):
        for thread,p in self.projections.items():
            rows=[row for row in p.messages if not self.confirmed(row,confirmations.get((thread,row['id'])))]
            p.retain({row['id'] for row in rows},{row['run_id'] for row in rows})
            self.steps[thread]={key:value for key,value in self.steps[thread].items() if key in {row['run_id'] for row in rows}}

    def view(self,thread_id=None,*,all_threads=False):
        rows=[]
        for thread,projection in self.projections.items():
            if all_threads or thread==thread_id or thread_id is None and self.sources[thread] is None:
                rows.extend(projection.messages)
        tools=[v for v in self.tools.values() if all_threads or v['thread_id']==thread_id or thread_id is None and v['source'] is None]
        return deepcopy(dict(epoch=self.epoch,seq=self.seq,replace=True,available=self.available,capture_incomplete=self.incomplete,
            usage_incomplete=self.usage_incomplete,dropped_events=self.dropped,messages=rows,tools=tools,
            threads=[dict(thread_id=k,source=v) for k,v in self.sources.items()],observed_runs=list(self.runs.values())))

    def publish(self,event,data):
        self.seq+=1
        if isinstance(data,dict) and 'messages' in data:data=data|dict(seq=self.seq)
        item=dict(seq=self.seq,event=event,data=data);size=len(canonical_json(item).encode())
        if size>self.limits.replay_bytes:
            item=dict(seq=self.seq,event='resync',data={'reason':'event_budget'});size=len(canonical_json(item).encode());self.incomplete=True
        self.ring.append((item,size));self.ring_bytes+=size
        while len(self.ring)>self.limits.replay_events or self.ring_bytes>self.limits.replay_bytes:
            _,removed=self.ring.popleft();self.ring_bytes-=removed
        if self.wake:self.wake.set()
