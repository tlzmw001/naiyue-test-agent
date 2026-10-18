"""One bounded optional usage writer, owned and drained by the application."""

import asyncio
from collections import deque
from dataclasses import asdict
import json
import logging
from threading import Lock

from naiyue_test_agent.agent_runtime.async_utils import run_blocking, await_settled
from naiyue_test_agent.contracts.failures import safe_error_type


logger = logging.getLogger(__name__)


class ModelUsagePersistence:
    def __init__(self, storage, subscribe, settings):
        self._storage, self._settings = storage, settings
        self._loop = asyncio.get_running_loop()
        self._lock = Lock()
        self._ready, self._changed = asyncio.Event(), asyncio.Event()
        self._items, self._runs = deque(), {}
        self._count = self._bytes = 0
        self._wake_pending = self._closing = self._failed = False
        self._close_task = None
        self._unsubscribe = subscribe(self.accept)
        self._task = asyncio.create_task(self._drain())
        self._task.add_done_callback(self._done)

    def _state(self, key):
        return self._runs.setdefault(key, {'pending': 0, 'reason': None, 'dropped_events': 0, 'disabled': False})

    def _wake(self):
        with self._lock:
            self._wake_pending = False
        self._ready.set()

    def _notify(self):
        if not self._wake_pending:
            self._wake_pending = True
            self._loop.call_soon_threadsafe(self._wake)

    def accept(self, event):
        if event.event_type not in ('model_invocation_started', 'model_invocation_finished'):
            return
        key = (event.session_id, event.run_id)
        try:
            size = len(json.dumps(asdict(event), ensure_ascii=False, allow_nan=False).encode('utf-8'))
        except Exception as error:
            logger.error('model_usage_event_invalid error_type=%s', safe_error_type(error))
            with self._lock:
                state = self._state(key)
                state['reason'], state['dropped_events'] = 'invalid_event', state['dropped_events'] + 1
            return
        with self._lock:
            state = self._state(key)
            if self._closing or self._failed or state['disabled']:
                state['reason'] = state['reason'] or 'writer_unavailable'
                state['dropped_events'] += 1
                return
            if (self._count >= self._settings.max_pending_events
                    or self._bytes + size > self._settings.max_pending_bytes):
                if state['reason'] is None:
                    logger.warning('model_usage_buffer_overflow')
                state['reason'] = state['reason'] or 'buffer_overflow'
                state['dropped_events'] += 1
                return
            self._items.append((event, size))
            self._count += 1
            self._bytes += size
            state['pending'] += 1
            self._notify()

    async def _drain(self):
        while True:
            with self._lock:
                if self._items:
                    event, size = self._items.popleft()
                elif self._closing:
                    return
                else:
                    self._ready.clear()
                    event = None
            if event is None:
                await self._ready.wait()
                continue
            key = (event.session_id, event.run_id)
            failure_reason = None
            try:
                await run_blocking(self._storage.save_model_usage_event, event)
            except Exception as error:
                failure_reason = 'storage_error'
                logger.error('model_usage_save_failed error_type=%s', safe_error_type(error))
            except BaseException:
                failure_reason = 'writer_unavailable'
                raise
            finally:
                with self._lock:
                    state = self._state(key)
                    state['pending'] -= 1
                    self._count -= 1
                    self._bytes -= size
                    if failure_reason is not None:
                        state['reason'], state['disabled'] = failure_reason, True
                        state['dropped_events'] += 1
                        kept = deque()
                        for queued, amount in self._items:
                            if (queued.session_id, queued.run_id) == key:
                                state['pending'] -= 1
                                state['dropped_events'] += 1
                                self._count -= 1
                                self._bytes -= amount
                            else:
                                kept.append((queued, amount))
                        self._items = kept
                self._changed.set()

    def _done(self, task):
        error = None if task.cancelled() else task.exception()
        if not task.cancelled() and error is None:
            return
        logger.error('model_usage_writer_failed error_type=%s', 'CancelledError' if task.cancelled() else safe_error_type(error))
        with self._lock:
            self._failed = True
            for state in self._runs.values():
                state['reason'] = state['reason'] or 'writer_unavailable'
                state['dropped_events'] += state['pending']
                state['pending'] = 0
            self._items.clear()
            self._count = self._bytes = 0
        self._changed.set()

    async def aflush_run(self, session_id, run_id):
        key = (session_id, run_id)
        while True:
            with self._lock:
                state = self._state(key)
                if state['pending'] == 0:
                    self._runs.pop(key)
                    report = {'capture_incomplete': state['reason'] is not None,
                              'reason': state['reason'], 'dropped_events': state['dropped_events']}
                    break
                self._changed.clear()
            await self._changed.wait()
        try:
            saved = await run_blocking(self._storage.finish_model_usage_run, session_id, run_id,
                reason=report['reason'], dropped_events=report['dropped_events'])
            if saved is not None:
                report = saved
        except Exception as error:
            report.update(capture_incomplete=True, reason='storage_error')
            logger.error('model_usage_finalize_failed error_type=%s', safe_error_type(error))
        if report['capture_incomplete']:
            logger.warning('model_usage_run_incomplete reason=%s dropped_events=%s', report['reason'], report['dropped_events'])
        return report

    async def _close(self):
        self._unsubscribe()
        with self._lock:
            self._closing = True
            self._notify()
        await asyncio.gather(self._task, return_exceptions=True)
        for session_id, run_id in tuple(self._runs):
            await self.aflush_run(session_id, run_id)

    async def aclose(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await await_settled(self._close_task)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
