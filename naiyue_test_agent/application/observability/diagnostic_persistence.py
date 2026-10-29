"""Optional finite diagnostics; execution and required saves keep their owners."""

import asyncio
from collections import deque
import json
import logging
import re
from threading import Lock

from naiyue_test_agent.agent_runtime.async_utils import run_blocking, await_settled
from naiyue_test_agent.contracts.failures import safe_error_type


logger = logging.getLogger(__name__)
_KINDS = frozenset(('run_started', 'run_finished', 'run_preparation',
    'model_http_attempt_started', 'model_http_attempt_finished',
    'model_call_started', 'model_call_finished', 'tool_call_started', 'tool_call_finished', 'approval_requested',
    'compaction_started', 'compaction_finished', 'context_recovery_started', 'context_recovery_finished',
    'control_accepted', 'control_delivered', 'control_applied', 'control_discarded', 'control_outcome_unknown',
    'message_completed', 'message_revised', 'message_snapshot_unavailable', 'turn_observation_unavailable'))
_LABELS = ('phase', 'mode', 'status', 'stop_mode', 'model_id', 'purpose', 'invocation_id', 'model_call_id',
    'tool_call_id', 'tool_name', 'boundary', 'outcome', 'reason', 'error_type', 'approval_batch_id',
    'control_id', 'kind', 'operation_id', 'operation', 'trigger', 'related_operation_id', 'origin_run_id',
    'message_id', 'message_type', 'visibility', 'attempt_id', 'http_capture_status')
_COUNTS = ('before_estimate', 'after_estimate', 'original_message_count', 'removed_message_count',
           'preserved_message_count', 'retry_attempts')


def project_diagnostic_event(event):
    if event.event_type not in _KINDS:
        return None
    if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', v)
           for v in (event.session_id, event.run_id)):
        raise ValueError('Invalid diagnostic identity')
    data, omitted = {}, False
    for name in _LABELS:
        value = getattr(event.data, name, None)
        if value is None:
            continue
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', value):
            data[name] = value
        else:
            omitted = True
    for name in _COUNTS:
        value = getattr(event.data, name, None)
        if type(value) is int and 0 <= value <= 2**63 - 1:
            data[name] = value
    if hasattr(event.data, 'duration_ms'):
        value = event.data.duration_ms
        valid = value is None or type(value) is int and 0 <= value <= 2**63 - 1
        data['duration_ms'] = value if valid else None
        omitted |= not valid
    for name, maximum, minimum in (('http_observation_version', 1, 1), ('http_status', 599, 100),
                                    ('response_headers_ms', 2**63 - 1, 0)):
        if hasattr(event.data, name):
            value = getattr(event.data, name)
            valid = value is None or type(value) is int and minimum <= value <= maximum
            data[name] = value if valid else None
            omitted |= not valid
    for name in ('manual_review_required',):
        value = getattr(event.data, name, None)
        if type(value) is bool:
            data[name] = value
    calls = getattr(event.data, 'tool_call_ids', ())
    if calls:
        data['tool_call_ids'] = [v for v in calls[:32] if isinstance(v, str)
                                 and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', v)]
        data['tool_call_count'] = len(calls)
        omitted |= len(data['tool_call_ids']) != len(calls)
    decisions = getattr(event.data, 'decisions', ())
    if decisions:
        data.update(approved_count=decisions.count('approve'), rejected_count=decisions.count('reject'))
    failure = getattr(event.data, 'failure', None)
    if failure is not None:
        data['failure'] = {name: getattr(failure, name) for name in ('stage', 'category', 'reason', 'error_type')}
    source = {name: getattr(event.source, name) for name in ('agent_path', 'agent_id', 'agent_run_id',
        'parent_run_id', 'parent_tool_call_id')} if event.source is not None else None
    if source is not None and any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,256}', v)
                                  for v in source.values()):
        raise ValueError('Invalid diagnostic source')
    if omitted:
        data['fields_omitted'] = True
    if type(event.sequence) is not int or not 0 < event.sequence <= 2**63 - 1:
        raise ValueError('Invalid diagnostic sequence')
    if not isinstance(event.timestamp, str) or not 1 <= len(event.timestamp) <= 64:
        raise ValueError('Invalid diagnostic timestamp')
    return {'event_type': event.event_type, 'session_id': event.session_id, 'run_id': event.run_id,
            'sequence': event.sequence, 'timestamp': event.timestamp, 'source': source, 'data': data}


class DiagnosticPersistence:
    def __init__(self, storage, subscribe, settings):
        self._storage, self._subscribe, self._settings = storage, subscribe, settings
        self._loop, self._lock = asyncio.get_running_loop(), Lock()
        self._ready = asyncio.Event()
        self._items, self._runs = deque(), {}
        self._count = self._bytes = 0
        self._closing = self._failed = self._wake_pending = False
        self._close_task = None

    def _wake(self):
        with self._lock:
            self._wake_pending = False
        self._ready.set()

    def _notify(self):
        if not self._wake_pending:
            self._wake_pending = True
            self._loop.call_soon_threadsafe(self._wake)

    def accept(self, event):
        try:
            record = project_diagnostic_event(event)
            if record is None:
                return
            size = len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        except Exception as error:
            logger.error('diagnostic_projection_failed error_type=%s', safe_error_type(error))
            key = (getattr(event, 'session_id', None), getattr(event, 'run_id', None))
            with self._lock:
                state = self._runs.get(key)
                if state is not None:
                    state['reason'], state['dropped'] = state['reason'] or 'invalid_event', state['dropped'] + 1
            return
        key = (record['session_id'], record['run_id'])
        with self._lock:
            if self._closing or self._failed:
                return
            if key not in self._runs:
                if len(self._runs) >= self._settings.max_pending_events:
                    logger.warning('diagnostic_run_capacity')
                    return
                self._runs[key] = {'pending': 0, 'reason': None, 'dropped': 0, 'disabled': False,
                                   'first': record, 'terminal': None, 'finishing': False}
            state = self._runs[key]
            if state['finishing']:
                return
            if record['event_type'] == 'run_finished' and record['source'] is None:
                state['terminal'] = record
            elif (state['disabled'] or self._count >= self._settings.max_pending_events
                  or self._bytes + size > self._settings.max_pending_bytes):
                state['reason'] = state['reason'] or ('storage_error' if state['disabled'] else 'buffer_overflow')
                state['dropped'] += 1
                logger.warning('diagnostic_buffer_overflow')
            else:
                self._items.append((record, size))
                self._count += 1
                self._bytes += size
                state['pending'] += 1
            self._notify()

    async def _finish(self, key, state):
        try:
            saved = await run_blocking(self._storage.diagnostics.finish_diagnostic_run,
                state['terminal'] or state['first'], self._settings,
                reason=state['reason'], dropped_events=state['dropped'])
            if not saved:
                logger.warning('diagnostic_summary_unavailable')
        except Exception as error:
            logger.error('diagnostic_finalize_failed error_type=%s', safe_error_type(error))

    async def _drain(self):
        while True:
            with self._lock:
                final = next(((key, value) for key, value in self._runs.items()
                              if value['pending'] == 0 and (value['terminal'] is not None or self._closing)), None)
                if final is not None:
                    final[1]['finishing'] = True
                    item = None
                elif self._items:
                    item = self._items.popleft()
                elif self._closing:
                    return
                else:
                    self._ready.clear()
                    item = None
            if final is not None:
                await self._finish(*final)
                with self._lock:
                    self._runs.pop(final[0])
                continue
            if item is None:
                await self._ready.wait()
                continue
            record, size = item
            key = (record['session_id'], record['run_id'])
            try:
                with self._lock:
                    disabled = self._runs[key]['disabled']
                saved = False if disabled else await run_blocking(self._storage.diagnostics.save_diagnostic_event, record, self._settings)
                if not saved:
                    with self._lock:
                        state = self._runs[key]
                        state['reason'], state['dropped'] = state['reason'] or 'workspace_budget', state['dropped'] + 1
                    logger.warning('diagnostic_event_not_saved')
            except Exception as error:
                logger.error('diagnostic_save_failed error_type=%s', safe_error_type(error))
                with self._lock:
                    state = self._runs[key]
                    state['reason'], state['disabled'] = 'storage_error', True
                    state['dropped'] += 1
            finally:
                with self._lock:
                    self._runs[key]['pending'] -= 1
                    self._count -= 1
                    self._bytes -= size

    def _done(self, task):
        if not task.cancelled() and task.exception() is None:
            return
        logger.error('diagnostic_writer_failed error_type=%s',
                     'CancelledError' if task.cancelled() else safe_error_type(task.exception()))
        with self._lock:
            self._failed = True
            self._items.clear()
            self._count = self._bytes = 0
            for state in self._runs.values():
                state['reason'] = state['reason'] or 'writer_unavailable'
                state['dropped'] += state['pending']
                state['pending'] = 0

    async def _close(self):
        try:
            self._unsubscribe()
        except Exception as error:
            logger.error('diagnostic_unsubscribe_failed error_type=%s', safe_error_type(error))
        with self._lock:
            self._closing = True
            self._notify()
        await asyncio.gather(self._task, return_exceptions=True)
        for key, state in tuple(self._runs.items()):
            await self._finish(key, state)
            self._runs.pop(key)

    async def aclose(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await await_settled(self._close_task)

    async def __aenter__(self):
        try:
            await run_blocking(self._storage.diagnostics.prepare_diagnostics, self._settings)
        except Exception as error:
            logger.error('diagnostic_prepare_failed error_type=%s', safe_error_type(error))
        self._unsubscribe = self._subscribe(self.accept)
        self._task = asyncio.create_task(self._drain())
        self._task.add_done_callback(self._done)
        return self

    async def __aexit__(self, *args):
        await self.aclose()
