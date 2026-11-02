"""Owner-loop live state and optional usage IO; required history stays independent."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import logging
import sqlite3

from pydantic import TypeAdapter,ValidationError
from naiyue_test_agent.contracts.cloud_tasks import CloudError,Label
from naiyue_test_agent.contracts.cloud_history import Identity
from naiyue_test_agent.contracts.cloud_events import canonical_json,digest
from .live_state import LiveTask
from .work import cloud_work

logger=logging.getLogger(__name__)


class CloudObservations:
    def __init__(self,runtime):
        self.runtime=runtime;self.limits=runtime.settings.observation_limits
        self.states=OrderedDict();self.leases=set();self.closing=False;self.loop=None
        self.deleted_tasks=set()

    def _loop(self):
        loop=asyncio.get_running_loop()
        if self.loop is not None and self.loop is not loop:
            if not self.loop.is_closed():raise CloudError('observation_loop_conflict',503)
            self.states.clear();self.leases.clear()
        self.loop=loop
        if self.closing:raise CloudError('cloud_closing',503)

    def _state(self,task_id):
        self._loop()
        if task_id in self.deleted_tasks:raise CloudError('task_not_available')
        if task_id not in self.states:
            if len(self.states)>=self.limits.cached_tasks:
                removable=next((key for key in self.states if not any(x.task_id==key for x in self.leases)),None)
                if removable is None:raise CloudError('observation_capacity',429)
                del self.states[removable]
            state=LiveTask(self.limits);state.wake=asyncio.Event();self.states[task_id]=state
        self.states.move_to_end(task_id)
        return self.states[task_id]

    def connected(self,task_id,connection_id,generation=None):
        state=self._state(task_id)
        if state.connection_id and state.connection_id!=connection_id:
            state.reset('connection_changed');state.publish('resync',{'reason':'connection_changed'})
        state.connection_id=connection_id;state.available=False
        if generation is not None:state.generation=generation

    def disconnected(self,task_id):
        if self.closing or task_id not in self.states:return
        state=self.states[task_id];state.available=False;state.incomplete=True
        state.publish('task_changed',{'reason':'runner_disconnected'})

    def changed(self,task_id,*,archive=None):
        if self.closing or task_id not in self.states:return
        self.states[task_id].publish('archive' if archive else 'task_changed',archive or {})

    def _admit(self,connection,frame):
        r=self.runtime;batch=frame.batch
        with r.gates.hold(connection.task_id):
            task=r.control.verify(connection,frame)
            if batch.task_id!=task.task_id:raise CloudError('task_identity_mismatch',403)
            if len(batch.records)>self.limits.batch_records or len(canonical_json(frame.model_dump()).encode())>r.settings.max_frame_bytes:
                raise CloudError('request_too_large',413)
            receipts={}
            for record in batch.records:
                if len(canonical_json(record.model_dump()).encode())>self.limits.record_bytes:raise CloudError('observation_record_too_large',413)
                if any(len(getattr(record.data,key,'' ) or '')>self.limits.text_chars for key in ('text','content','reasoning')):
                    raise CloudError('observation_text_too_large',413)
                if record.run_id not in receipts:receipts[record.run_id]=r.registry.run_command(task.task_id,record.run_id)
                receipt=receipts[record.run_id]
                if (receipt.expected_generation or 0)>task.generation:raise CloudError('observation_generation_mismatch')
                if record.event_type=='model_call_started' and record.data.model_id is not None and record.data.model_id not in task.runner_model_ids:
                    raise CloudError('observation_model_mismatch')

    def _save_usage(self,connection,frame,identities):
        with self.runtime.gates.hold(connection.task_id):
            # Authentication/fence failures are mandatory rejection, outside
            # the optional storage catch below.
            self.runtime.control.verify(connection,frame)
            try:history=self.runtime._history(connection.task_id)
            except (OSError,sqlite3.Error,CloudError,ValueError) as error:
                logger.warning('cloud_usage_capture_incomplete error_type=%s',type(error).__name__)
                return dict(confirmations={},usage_incomplete=True,confirmation_incomplete=True)
            usage_incomplete=False
            try:history.usage.save(frame.batch)
            except (OSError,sqlite3.Error,CloudError,ValueError) as error:
                usage_incomplete=True
                logger.warning('cloud_usage_capture_incomplete error_type=%s',type(error).__name__)
            try:
                confirmations=history.confirmations(identities,read_timeout=self.runtime.settings.history_read_timeout_seconds)
            except (OSError,sqlite3.Error,CloudError,ValueError) as error:
                logger.warning('cloud_live_confirmation_incomplete error_type=%s',type(error).__name__)
                return dict(confirmations={},usage_incomplete=usage_incomplete,confirmation_incomplete=True)
            return dict(confirmations=confirmations,usage_incomplete=usage_incomplete,confirmation_incomplete=False)

    async def receive(self,connection,frame):
        self._loop()
        await cloud_work(self.runtime,lambda:self._admit(connection,frame))
        state=self._state(connection.task_id)
        if state.connection_id not in (None,connection.connection_id):raise CloudError('runner_connection_stale')
        state.connection_id=connection.connection_id
        state.generation=frame.generation
        fingerprint=digest(frame.batch.model_dump())
        if not state.accept(frame.batch,fingerprint):return
        identities=state.identities()+[(item.thread_id,getattr(item.data,'message_id',getattr(item.data,'id',None)))
            for item in frame.batch.records if item.event_type.startswith('message_')]
        captured=await cloud_work(self.runtime,lambda:self._save_usage(connection,frame,identities))
        if state.connection_id!=connection.connection_id:raise CloudError('runner_connection_stale')
        if self.closing:return
        state.usage_incomplete|=captured['usage_incomplete']
        if captured['confirmation_incomplete']:
            state.reset('confirmation_unavailable');state.publish('resync',{'reason':'confirmation_unavailable'});return
        try:
            state.apply(frame.batch);state.discard_confirmed(captured['confirmations'])
            state.publish('update',state.view(all_threads=True))
        except (ValueError,RuntimeError,KeyError) as error:
            state.reset('projection_failed');state.publish('resync',{'reason':'projection_failed'})
            logger.warning('cloud_live_projection_incomplete error_type=%s',type(error).__name__)

    async def usage(self,task_id,*,limit=None,after_model=None):
        limit=self.runtime.archive._limit(limit)
        if limit>100:raise CloudError('invalid_usage_page',422)
        if after_model is not None:
            try:TypeAdapter(Label).validate_python(after_model)
            except ValidationError:raise CloudError('invalid_usage_page',422) from None
        def read():
            with self.runtime.gates.hold(task_id):
                task=self.runtime.registry.get_task(task_id)
                history=self.runtime._history(task_id)
                value=history.usage.read(limit=limit,after_model=after_model,
                    read_timeout=self.runtime.settings.history_read_timeout_seconds)
                if task.last_run_id and not value['capture']['recorded_runs']:value['capture']['capture_incomplete']=True
                if history.usage_reopen_incomplete or value['capture']['open_runs']>int(task.active_run_id is not None):
                    value['capture']['capture_incomplete']=True
                return value
        try:value=await cloud_work(self.runtime,read)
        except (OSError,sqlite3.Error,ValueError) as error:
            logger.warning('cloud_usage_read_unavailable error_type=%s',type(error).__name__)
            raise CloudError('usage_unavailable',503) from error
        state=self.states.get(task_id)
        if state and state.usage_incomplete:value['capture']['capture_incomplete']=True
        value['task_id']=task_id
        if len(canonical_json(value).encode())>self.runtime.settings.max_frame_bytes:raise CloudError('usage_view_too_large',413)
        return value

    async def _read_snapshot(self,task_id,thread_id,limit,identities):
        def read():
            r=self.runtime
            with r.gates.hold(task_id):
                task=r.registry.get_task(task_id)
                view={k:getattr(task,k) for k in ('task_id','generation','revision','lifecycle','runner_phase',
                    'active_run_id','last_run_id','last_error_code','control_operation_id')}
            archive=r.archive.page(task_id,thread_id=thread_id,limit=limit)
            with r.gates.hold(task_id):
                confirmations=r._history(task_id).confirmations(identities,upper=archive['coverage_seq'],read_timeout=r.settings.history_read_timeout_seconds)
            return view,archive,confirmations
        return await cloud_work(self.runtime,read)

    async def snapshot(self,task_id,*,thread_id=None,limit=None):
        if thread_id is not None:
            try:TypeAdapter(Identity).validate_python(thread_id)
            except ValidationError:raise CloudError('invalid_history_identity',422) from None
        count=min(self.runtime.archive._limit(limit),self.limits.snapshot_messages)
        await cloud_work(self.runtime,lambda:self.runtime.available_task(task_id))
        state=self._state(task_id)
        for _ in range(self.limits.snapshot_attempts):
            seq=state.seq;live=state.view(thread_id)
            task,archive,confirmations=await self._read_snapshot(task_id,thread_id,count,state.identities())
            if state.seq!=seq:continue
            if state.generation is not None and state.generation!=task['generation']:
                state.reset('generation_changed');state.available=False;state.generation=task['generation'];continue
            live['messages']=[row for row in live['messages'] if not state.confirmed(row,confirmations.get((row['thread_id'],row['id'])))]
            state.discard_confirmed(confirmations)
            live['messages']=[self._trim(row) for row in live['messages']]
            result=dict(task=task,archive=archive,live=live,
                cursor=self._cursor(task_id,thread_id,state,seq))
            from .observation_stream import encode_sse
            budget=self.runtime.settings.max_frame_bytes
            def size():return len(encode_sse(dict(event='snapshot',id=result['cursor'],data=result)))
            while size()>budget and (live['messages'] or live['tools']):
                if live['messages']:live['messages'].pop(0)
                else:live['tools'].pop(0)
                live['capture_incomplete']=True
            while size()>budget and len(archive['messages'])>1:
                archive['messages'].pop(0)
                archive['next_cursor']=self.runtime.archive._cursor(task_id,'messages',archive['thread']['thread_id'],
                    archive,archive['messages'][0]['sequence'])
            if size()>budget:raise CloudError('snapshot_too_large',413)
            return result
        raise CloudError('snapshot_busy',503)

    def _trim(self,row):
        value=deepcopy(row)
        for key in ('content','reasoning'):
            if isinstance(value.get(key),str) and len(value[key])>self.limits.text_chars:
                value[key]=value[key][-self.limits.text_chars:];value[key+'_truncated']=True
        return value

    def _cursor(self,task_id,thread_id,state,seq):
        import base64,time
        value=dict(task_id=task_id,thread=thread_id,instance=self.runtime.instance_id,epoch=state.epoch,seq=seq,
            expires=time.time()+self.limits.cursor_seconds)
        raw=canonical_json(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip('=')+'.'+self.runtime.archive._sign(b'live\0'+raw)

    def _decode(self,cursor,task_id,thread_id):
        import base64,hmac,json,time
        try:
            if len(cursor)>4096:raise ValueError()
            encoded,signature=cursor.split('.')
            raw=base64.b64decode(encoded+'='*(-len(encoded)%4),altchars=b'-_',validate=True)
            if not hmac.compare_digest(signature,self.runtime.archive._sign(b'live\0'+raw)):raise ValueError()
            value=json.loads(raw)
            if (value['task_id']!=task_id or value['thread']!=thread_id or value['instance']!=self.runtime.instance_id
                    or value['expires']<time.time()):raise ValueError()
            return value
        except (ValueError,KeyError,TypeError,UnicodeError):raise CloudError('invalid_observation_cursor',422) from None

    async def subscribe(self,task_id,*,thread_id=None,last_event_id=None):
        self._loop()
        selected=self._decode(last_event_id,task_id,thread_id) if last_event_id else None
        if len(self.leases)>=self.limits.subscribers or sum(x.task_id==task_id for x in self.leases)>=self.limits.task_subscribers:
            raise CloudError('observation_subscriber_capacity',429)
        # Validate registered task/snapshot before exposing a response/lease.
        snapshot=await self.snapshot(task_id,thread_id=thread_id)
        if self.closing:raise CloudError('cloud_closing',503)
        if len(self.leases)>=self.limits.subscribers or sum(x.task_id==task_id for x in self.leases)>=self.limits.task_subscribers:
            raise CloudError('observation_subscriber_capacity',429)
        from .observation_stream import ObservationLease
        lease=ObservationLease(self,task_id,thread_id,self._state(task_id),snapshot,selected)
        self.leases.add(lease)
        return lease

    async def aclose(self):
        self.closing=True
        for lease in tuple(self.leases):await lease.aclose()

    def delete_task(self,task_id):
        self.deleted_tasks.add(task_id)
        for lease in tuple(self.leases):
            if lease.task_id==task_id:
                lease.deleting=True;lease.state.wake.set()
        self.states.pop(task_id,None)
