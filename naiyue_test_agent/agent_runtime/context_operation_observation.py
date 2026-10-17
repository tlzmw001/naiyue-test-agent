"""Observe bounded context operations without owning compaction or retries."""


import logging
from collections import deque
from dataclasses import asdict
from threading import RLock, Lock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphBubbleUp

from .call_observation import event_label, token_count
from .context_events import ContextOperationStartedData, ContextOperationFinishedData, safe_context_reason
from naiyue_test_agent.contracts.failures import safe_error_type


logger = logging.getLogger(__name__)


class ContextOperationObserver:
    def __init__(self, publisher):
        self.publisher = publisher
        self._records = {}
        self._outbox = deque()
        self._lock = RLock()
        self._send_lock = Lock()
        self._disabled = False
        self._current_call = None
        self._active_recovery = None

    def model_call_started(self, call_id):
        self.flush()
        with self._lock:
            self._current_call = call_id

    def _begin(self, operation, model_id, trigger, estimate=None, count=None):
        op = uuid4().hex
        data = ContextOperationStartedData(op, operation, event_label(model_id), trigger,
                                           self._current_call, token_count(estimate), token_count(count))
        self._records[op] = {'start': data, 'metrics': {}, 'stage': 'cut' if operation == 'compaction' else 'compact'}
        self._outbox.append(('compaction_started' if operation == 'compaction' else 'context_recovery_started', data))
        return op

    def begin_compaction(self, model_id, trigger, estimate, count):
        with self._lock:
            op = self._begin('compaction', model_id, trigger, estimate, count)
            if trigger == 'overflow' and self._active_recovery in self._records:
                self._records[self._active_recovery]['metrics']['related_operation_id'] = op
                self._records[op]['parent'] = self._active_recovery
        self.flush()
        return op

    def summary_started(self, op):
        with self._lock:
            if op in self._records:
                self._records[op]['stage'] = 'summary'

    def summary_finished(self, op, duration_ms, usage=None, error=None):
        with self._lock:
            if op not in self._records:
                return
            record = self._records[op]
            record['metrics'].update(summary_duration_ms=token_count(duration_ms),
                                     summary_input_tokens=token_count(usage.get('input_tokens') if usage else None),
                                     summary_output_tokens=token_count(usage.get('output_tokens') if usage else None))
            if error is not None:
                record['error_type'] = safe_error_type(error)

    def prepare_compaction(self, op, summary, preserved, removed, estimate):
        with self._lock:
            if op not in self._records:
                return
            record = self._records[op]
            record.update(summary=summary, kept=tuple(m.id for m in preserved),
                          removed=tuple(m.id for m in removed), stage='state')
            record['metrics'].update(after_estimate=token_count(estimate),
                                     removed_message_count=len(removed), preserved_message_count=len(preserved))

    def finish_compaction(self, op, status, reason=None, error=None):
        with self._lock:
            self._close(op, status, reason, error=error)

    def begin_recovery(self, model_id):
        with self._lock:
            op = self._begin('recovery', model_id, 'overflow')
            self._active_recovery = op
        self.flush()
        return op

    def retry_started(self, op):
        with self._lock:
            if op in self._records:
                self._records[op]['stage'] = 'retry'
                self._records[op]['metrics']['retry_attempts'] = 1

    def retry_returned(self, op, response):
        with self._lock:
            if op in self._records:
                self._records[op]['retry'] = next((m for m in response.result if isinstance(m, AIMessage)), None)

    def fail_recovery(self, op, stage, error):
        with self._lock:
            self._close(op, 'failed', 'unclassified', error=error, stage=stage)

    def model_call_accepted(self, call_id):
        with self._lock:
            for record in self._records.values():
                if record['start'].operation == 'recovery' and record['start'].model_call_id == call_id:
                    record['accepted'] = True

    def model_call_failed(self, call_id, error):
        with self._lock:
            if isinstance(error, GraphBubbleUp):
                return
            for op, record in list(self._records.items()):
                if record['start'].operation == 'recovery' and record['start'].model_call_id == call_id:
                    self._close(op, 'failed', getattr(error, 'reason', 'unclassified'),
                                error=error, stage='response_validation')

    def observe(self, messages, *, visibility='graph_values'):
        if not isinstance(messages, (list, tuple)):
            return
        ids = tuple(getattr(m, 'id', None) for m in messages)
        ai_ids = {m.id for m in messages if isinstance(m, AIMessage) and isinstance(m.id, str) and m.id}
        with self._lock:
            for op, record in list(self._records.items()):
                summary = record.get('summary')
                if summary is None or not isinstance(summary.id, str) or not summary.id:
                    continue
                prefix = (summary.id, *record['kept'])
                if (not messages or not isinstance(messages[0], HumanMessage)
                        or not all(isinstance(key, str) and key for key in (*prefix, *record['removed']))
                        or ids[:len(prefix)] != prefix or set(record['removed']) & set(ids)):
                    continue
                parent = record.get('parent')
                self._close(op, 'applied', visibility=visibility, summary_id=summary.id)
                if parent in self._records:
                    self._records[parent].update(replacement=summary.id)
            for op, record in list(self._records.items()):
                retry = record.get('retry')
                if (record['start'].operation == 'recovery' and record.get('accepted')
                        and record.get('replacement') and retry is not None
                        and isinstance(retry.id, str) and retry.id and retry.id in ai_ids):
                    self._close(op, 'recovered', visibility=visibility, summary_id=record['replacement'])
        self.flush()

    def _close(self, op, status, reason=None, *, error=None, stage=None, visibility=None, summary_id=None):
        record = self._records.get(op)
        if record is None:
            return
        data = ContextOperationFinishedData(
            **asdict(record['start']), **record['metrics'], status=status,
            stage=stage or record['stage'], reason=safe_context_reason(reason) if reason is not None else None,
            error_type=record.get('error_type') or (safe_error_type(error) if error is not None else None),
            summary_message_id=summary_id, visibility=visibility,
        )
        self._outbox.append(('compaction_finished' if data.operation == 'compaction' else 'context_recovery_finished', data))
        del self._records[op]
        if self._active_recovery == op:
            self._active_recovery = None

    def finalize(self, error=None):
        with self._lock:
            if error is not None and (not isinstance(error, Exception) or isinstance(error, GraphBubbleUp)):
                self._records.clear()
                self._outbox.clear()
                return
            for op, record in list(self._records.items()):
                reason = ('state_replacement_unconfirmed' if record['start'].operation == 'compaction'
                          else 'retry_response_unconfirmed')
                self._close(op, 'unconfirmed', reason, error=error, stage='state')
        self.flush()

    def flush(self):
        with self._send_lock:
            while True:
                with self._lock:
                    if self._disabled:
                        self._outbox.clear()
                        return
                    if not self._outbox:
                        return
                    event_type, data = self._outbox.popleft()
                try:
                    self.publisher.publish(event_type, data)
                except GraphBubbleUp:
                    raise
                except Exception as error:
                    with self._lock:
                        self._disabled = True
                        self._outbox.clear()
                    logger.error('context_event_publish_failed error_type=%s', safe_error_type(error))
                    return
