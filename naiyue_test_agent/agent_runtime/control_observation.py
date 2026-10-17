"""Per-run control observation with FIFO delivery outside application locks."""


import logging
from collections import deque
from dataclasses import replace
from threading import Lock
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.contracts.session_events import ControlData, ApprovalRequestedData
from naiyue_test_agent.contracts.failures import safe_error_type


logger = logging.getLogger(__name__)


class ControlObserver:
    def __init__(self, publisher):
        self.publisher = publisher
        self._records = {}
        self._queue_ids = {'steering': deque(), 'follow_up': deque()}
        self._batches = deque()
        self._outbox = deque()
        self._record_lock = Lock()
        self._send_lock = Lock()
        self._ready = False
        self._closed = False
        self._disabled = False
        self._approval_id = None
        self._approval_candidates = set()
        self._stop_id = None

    def start(self):
        with self._record_lock:
            self._ready = True
        self.flush()

    def accept(self, kind, *, queue_size=0, tool_call_ids=(), decisions=(), origin_run_id=None):
        with self._record_lock:
            if self._closed:
                return
            if kind == 'stop' and self._stop_id is not None:
                return
            control_id = uuid4().hex
            data = ControlData(control_id, kind, tool_call_ids=tool_call_ids,
                               decisions=decisions, origin_run_id=origin_run_id)
            self._records[control_id] = {'data': data, 'phase': 'accepted', 'message_id': None}
            if kind in self._queue_ids:
                ids = self._queue_ids[kind]
                while len(ids) < queue_size:
                    ids.append(None)  # private, untracked queue entries are not invented controls
                ids.append(control_id)
            elif kind == 'stop':
                self._stop_id = control_id
            else:
                self._approval_id = control_id
            self._outbox.append(('control_accepted', data))

    def select(self, kind, count):
        with self._record_lock:
            batch = []
            ids = self._queue_ids[kind]
            for _ in range(count):
                control_id = ids.popleft() if ids else None
                message_id = uuid4().hex if control_id else None
                batch.append((control_id, message_id))
                if control_id:
                    record = self._records[control_id]
                    record.update(phase='selected', message_id=message_id)
                    self._outbox.append(('control_delivery_started', replace(record['data'], boundary='queue')))
            if batch:
                self._batches.clear()
                self._batches.append(batch)

    def prepare_messages(self, texts):
        with self._record_lock:
            batch = self._batches.popleft() if self._batches else []
        if len(batch) != len(texts):
            logger.error('control_message_batch_unavailable')
            return [HumanMessage(content=text) for text in texts]
        return [HumanMessage(id=message_id, content=text)
                for text, (_control_id, message_id) in zip(texts, batch, strict=True)]

    def observe(self, messages, *, visibility='graph_values'):
        visible = {m.id for m in messages if isinstance(m, HumanMessage)}
        with self._record_lock:
            for control_id, record in list(self._records.items()):
                if (record['data'].kind in self._queue_ids and record['phase'] == 'selected'
                        and record['message_id'] in visible):
                    self._outbox.append(('control_delivered', replace(
                        record['data'], boundary='message_state', message_id=record['message_id'],
                        visibility=visibility,
                    )))
                    del self._records[control_id]
        self.flush()

    def submit_approval(self):
        with self._record_lock:
            record = self._records.get(self._approval_id)
            if record and record['phase'] == 'accepted':
                record['phase'] = 'selected'
                self._outbox.append(('control_delivery_started', replace(record['data'], boundary='invocation')))
        self.flush()

    def begin_approval_hook(self):
        with self._record_lock:
            self._approval_candidates.clear()

    def note_approval_candidate(self, tool_call_id):
        with self._record_lock:
            self._approval_candidates.add(tool_call_id)

    def approval_applied(self):
        with self._record_lock:
            record = self._records.get(self._approval_id)
            if (record and record['phase'] == 'selected' and self._approval_candidates
                    and self._approval_candidates == set(record['data'].tool_call_ids)):
                self._outbox.append(('control_applied', replace(record['data'], boundary='approval_hook')))
                del self._records[self._approval_id]
        self.flush()

    def run_result(self, result):
        with self._record_lock:
            record = self._records.get(self._stop_id)
            if record and isinstance(result, dict) and result.get('run_stopped') is True:
                self._outbox.append(('control_applied', replace(record['data'], boundary='active')))
                del self._records[self._stop_id]
        self.flush()

    def approval_requested(self, tool_call_ids):
        with self._record_lock:
            self._outbox.append(('approval_requested', ApprovalRequestedData(tool_call_ids)))
        self.flush()

    def close(self, reason):
        """Record cleanup facts only; safe to call while holding the session lock."""
        with self._record_lock:
            self._closed = True
            for record in self._records.values():
                selected = record['phase'] == 'selected'
                unknown_reason = ('approval_resolution_unconfirmed' if record['data'].kind == 'approval'
                                  else 'message_state_unconfirmed')
                self._outbox.append(('control_outcome_unknown' if selected else 'control_discarded',
                                     replace(record['data'], reason=unknown_reason if selected else reason)))
            self._records.clear()
            self._batches.clear()
            for ids in self._queue_ids.values():
                ids.clear()

    def flush(self):
        with self._send_lock:
            while True:
                with self._record_lock:
                    if self._disabled:
                        self._outbox.clear()
                        return
                    if not self._ready or not self._outbox:
                        return
                    event_type, data = self._outbox.popleft()
                try:
                    self.publisher.publish(event_type, data)
                except GraphBubbleUp:
                    raise
                except Exception as error:
                    with self._record_lock:
                        self._disabled = True
                        self._outbox.clear()
                    logger.error('control_event_publish_failed error_type=%s', safe_error_type(error))
                    return
