"""Optional bounded queue: producer never performs IO or waits for the network."""
from collections import deque
import logging
from threading import Lock
from uuid import uuid4

from pydantic import TypeAdapter
from naiyue_test_agent.contracts.cloud_observation import Observation
from naiyue_test_agent.contracts.cloud_events import canonical_json
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .observation_encoding import encode_event

logger=logging.getLogger(__name__)


class RunnerObservations:
    def __init__(self,bootstrap):
        self.bootstrap=bootstrap;self.limits=bootstrap.observation_limits;self.epoch=str(uuid4())
        self.root_thread=None;self._lock=Lock();self._items=deque();self._bytes=0
        self._next=1;self._dropped=0;self._unsubscribe=None;self._closed=False

    def bind(self,sessions,root_thread):
        if self._unsubscribe:self._unsubscribe()
        self.root_thread=root_thread
        self._unsubscribe=sessions.subscribe_events(self,session_id=self.bootstrap.task_id)

    def close(self):
        if self._unsubscribe:self._unsubscribe();self._unsubscribe=None
        with self._lock:self._closed=True

    def __call__(self,event):
        if event.session_id!=self.bootstrap.task_id:return
        try:
            thread='subagent:'+event.source.agent_id if event.source else self.root_thread
            value=encode_event(event,thread,self.limits)
            if value is None:return
            value=TypeAdapter(Observation).validate_python(value).model_dump(mode='json')
            size=len(canonical_json(value).encode())
            if size>min(self.limits.record_bytes,self.bootstrap.max_frame_bytes-1536):raise ValueError('record_budget')
        except (ValueError,TypeError,KeyError,AttributeError,CloudError) as error:
            with self._lock:self._next+=1;self._dropped+=1
            logger.warning('runner_observation_dropped error_type=%s',type(error).__name__)
            return
        with self._lock:
            if self._closed:return
            if self._items and event.event_type in ('message_delta','message_reasoning_delta'):
                seq,old,amount=self._items[-1]
                if (all(old[k]==value[k] for k in ('event_type','run_id','thread_id','source'))
                        and old['data']['message_id']==value['data']['message_id']
                        and old['data']['reply_id']==value['data']['reply_id']
                        and old['data']['end_index']+1==value['data']['index']):
                    merged=old|{'data':old['data']|dict(text=old['data']['text']+value['data']['text'],end_index=value['data']['end_index'])}
                    encoded=len(canonical_json(merged).encode())
                    if (len(merged['data']['text'])<=self.limits.text_chars and encoded<=self.limits.record_bytes
                            and encoded<=self.bootstrap.max_frame_bytes-1536 and self._bytes-amount+encoded<=self.limits.queue_bytes):
                        self._items[-1]=(seq,merged,encoded);self._bytes+=encoded-amount;return
            seq=self._next;self._next+=1
            if len(self._items)>=self.limits.queue_records or self._bytes+size>self.limits.queue_bytes:
                self._dropped+=len(self._items);self._items.clear();self._bytes=0
            if size>self.limits.queue_bytes:
                self._dropped+=1;return
            self._items.append((seq,value,size));self._bytes+=size

    def drain(self):
        with self._lock:
            if not self._items and not self._dropped:return None
            first=self._items[0][0] if self._items else self._next
            result=dict(task_id=self.bootstrap.task_id,epoch=self.epoch,first_seq=first,dropped=self._dropped,records=[])
            used=len(canonical_json(result).encode())+1024
            while self._items and len(result['records'])<self.limits.batch_records:
                seq,item,size=self._items[0]
                if seq!=first+len(result['records']):break
                if used+size>self.bootstrap.max_frame_bytes:break
                self._items.popleft();self._bytes-=size;used+=size+1;result['records'].append(item)
            self._dropped=0
            return result
