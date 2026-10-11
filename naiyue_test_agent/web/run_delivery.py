"""Own HTTP jobs and bounded event replay; never execute an Agent loop here."""

import asyncio
from collections import OrderedDict, deque
from copy import deepcopy
from hashlib import sha256
import json
import logging
from threading import Lock

from langchain_core.messages import HumanMessage

from naiyue_test_agent.application.stream_projection import StreamingMessageProjection
from .errors import WebError, public_error
from .serialization import encoded_size, wire_event, text_content, message_view


logger = logging.getLogger(__name__)


class DeliveredRun:
    def __init__(self, session_id, rows, settings, on_started):
        self.session_id, self.settings = session_id, settings
        self.run_id = None
        self.started = asyncio.get_running_loop().create_future()
        self.started.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        self.changed = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.lock = Lock()
        self.events = deque(maxlen=settings.event_capacity)
        self.projection = StreamingMessageProjection()
        self.projection.seed(session_id, rows)
        self.last_sequence = 0
        self.status = 'running'
        self.manual_review_required = False
        self.failure = None
        self.retained = []
        self.tool_states = {}
        self.done = False
        self.task = None
        self.unsubscribe = None
        self.wake_pending = False
        self.on_started = on_started
        self._registered = False

    def _wake(self):
        with self.lock:
            self.wake_pending = False
        self.changed.set()

    def _ready(self, run_id):
        if not self._registered:
            self.on_started(self)
            self._registered = True
        if not self.started.done():
            self.started.set_result(run_id)

    def observe(self, event):
        if event.session_id != self.session_id:
            return
        with self.lock:
            if self.run_id is None:
                self.run_id = event.run_id
                if event.event_type != 'run_started':
                    self.projection.incomplete = True
                self.loop.call_soon_threadsafe(self._ready, event.run_id)
            if event.sequence != self.last_sequence + 1:
                self.projection.incomplete = True
            if self.run_id != event.run_id:
                return
            self.last_sequence = event.sequence
            try:
                payload = wire_event(event)
                if encoded_size(payload) > self.settings.max_frame_bytes:
                    self.projection.incomplete = True
                else:
                    self.events.append(payload)
                    self.projection(event)
                    if event.event_type in ('tool_call_started', 'tool_call_finished'):
                        data = payload['data']
                        self.tool_states[data['tool_call_id']] = {**data,
                            'run_id': event.run_id,
                            'state': 'running' if event.event_type == 'tool_call_started' else 'finished'}
                if event.event_type == 'run_finished':
                    self.status = event.data.status
                    self.manual_review_required = event.data.manual_review_required
                    self.failure = payload['data']['failure']
                    for row in self.tool_states.values():
                        if row['state'] == 'running':
                            row['state'] = 'unconfirmed'
            except Exception as error:
                self.projection.incomplete = True
                logger.error('web_event_projection_failed error_type=%s', type(error).__name__)
            if not self.wake_pending:
                self.wake_pending = True
                self.loop.call_soon_threadsafe(self._wake)

    def view(self):
        with self.lock:
            value = {'session_id': self.session_id, 'run_id': self.run_id, 'status': self.status,
                'messages': [{**deepcopy(row), 'content': text_content(row['content'])}
                             for row in self.projection.messages], 'last_sequence': self.last_sequence,
                'incomplete': self.projection.incomplete, 'done': self.done,
                'manual_review_required': self.manual_review_required, 'failure': self.failure,
                'retained': deepcopy(self.retained), 'tool_states': deepcopy(self.tool_states)}
        if encoded_size(value) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return value

    def _check_cursor(self, after):
        if after > self.last_sequence or self.projection.incomplete or (
                self.events and after < self.events[0]['sequence'] - 1):
            raise WebError('resync_required')

    def check_cursor(self, after):
        with self.lock:
            self._check_cursor(after)

    async def stream(self, after):
        while True:
            with self.lock:
                try:
                    self._check_cursor(after)
                except WebError:
                    yield_gap = True
                    batch, done = [], True
                else:
                    yield_gap = False
                    batch = [e for e in self.events if e['sequence'] > after]
                    done = self.done
                    if not batch and not done:
                        self.changed.clear()
            if yield_gap:
                yield {'event': 'resync_required', 'data': {'code': 'resync_required'}}
                return
            for event in batch:
                after = event['sequence']
                yield {'event': 'runtime', 'id': str(after), 'data': event}
            if done:
                return
            if not batch:
                await self.changed.wait()


class RunDelivery:
    def __init__(self, application, settings):
        self.application, self.settings = application, settings
        self.runs = OrderedDict()
        self.latest = {}
        self.receipts = {}
        self.pending_receipts = {}
        self.start_lock = asyncio.Lock()
        self.control_lock = asyncio.Lock()
        self.active = None

    def get(self, run_id):
        try:
            return self.runs[run_id]
        except KeyError:
            raise WebError('run_not_found', 404) from None

    def _cached(self, scope, request_id, body):
        fingerprint = sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        key = (scope, request_id)
        old = self.receipts.get(key)
        if old is not None and old[0] != fingerprint:
            raise WebError('request_id_conflict')
        if (old is None and key not in self.pending_receipts
                and len(self.receipts) + len(self.pending_receipts) >= self.settings.max_receipts):
            raise WebError('request_window_full')
        return key, fingerprint, deepcopy(old[1]) if old else None

    async def start(self, session_id, body, rows, *, can_run=True):
        return await self._launch(session_id, body, rows, can_run=can_run)

    async def resume(self, session_id, body, check_current):
        return await self._launch(session_id, body, [], resume_check=check_current)

    async def _launch(self, session_id, body, rows, *, can_run=True, resume_check=None):
        async with self.start_lock:
            key, fingerprint, old = self._cached(session_id, body.request_id, body.model_dump())
            if old is not None:
                return old
            pending = self.pending_receipts.get(key)
            if pending is not None:
                if pending[0] != fingerprint:
                    raise WebError('request_id_conflict')
                run_id = await asyncio.shield(pending[1].started)
                return {'session_id': session_id, 'run_id': run_id}
            if self.active is not None and not self.active.task.done():
                raise WebError('session_busy')
            if not can_run:
                raise WebError('session_recovery_blocked')
            if resume_check is not None:
                rows = await resume_check()

            def register(job):
                self.runs[job.run_id] = job
                self.latest[session_id] = job.run_id
                self.receipts[key] = (fingerprint, {'session_id': session_id, 'run_id': job.run_id})
                self.pending_receipts.pop(key, None)
                self._prune()

            job = DeliveredRun(session_id, rows, self.settings, register)
            previous = self.runs.get(self.latest.get(session_id))
            if previous is not None:
                job.tool_states = previous.view()['tool_states']
            self.pending_receipts[key] = (fingerprint, job)
            job.unsubscribe = self.application.subscribe_events(job.observe)
            self.active = job
            job.task = asyncio.create_task(self._produce(job, body, resume=resume_check is not None))
            # Even when no event reveals the ID, keep the pending receipt so
            # retrying this request cannot repeat an already completed run.
            run_id = await asyncio.shield(job.started)
            return {'session_id': session_id, 'run_id': run_id}

    def _prune(self):
        finished = [key for key, job in self.runs.items() if job.done]
        for key in finished[:-self.settings.retained_runs]:
            old = self.runs.pop(key)
            if self.latest.get(old.session_id) == key:
                self.latest.pop(old.session_id, None)

    async def _produce(self, job, body, *, resume=False):
        try:
            if resume:
                result = await self.application.aresume_tool_approval(job.session_id, [item.decision for item in body.decisions])
            else:
                result = await self.application.arun(job.session_id,
                    [HumanMessage(content=body.text, id=body.request_id)])
            if job.status == 'running':
                with job.lock:
                    job.status = ('interrupted' if result.get('approval_requests') else
                                  'stopped' if result.get('run_stopped') is True else 'completed')
                    job.projection.incomplete = True
                    job.projection.seed(job.session_id, [message_view(m) for m in result.get('messages', [])])
                    for row in job.tool_states.values():
                        if row['state'] == 'running':
                            row['state'] = 'unconfirmed'
        except BaseException as error:
            if job.run_id is not None:
                job._ready(job.run_id)
            if not job.started.done():
                job.started.set_exception(public_error(error))
            logger.error('web_run_failed error_type=%s', type(error).__name__)
            with job.lock:
                if job.status == 'running':
                    job.status = 'unconfirmed'
                    job.projection.incomplete = True
        finally:
            if not job.started.done():
                if job.run_id is not None:
                    job._ready(job.run_id)
                else:
                    job.started.set_exception(WebError('run_observation_unavailable', 503))
            job.unsubscribe()
            with job.lock:
                job.done = True
            job.changed.set()

    async def control(self, run_id, body):
        async with self.control_lock:
            key, fingerprint, old = self._cached(run_id, body.request_id, body.model_dump())
            if old is not None:
                return old
            job = self.get(run_id)
            if job is not self.active or job.done or job.status != 'running':
                raise WebError('stale_run')
            try:
                if body.kind == 'stop':
                    retained = self.application.request_stop(job.session_id, run_id=run_id)
                    with job.lock:
                        job.retained = list(retained)
                else:
                    method = self.application.steer if body.kind == 'steering' else self.application.follow_up
                    method(job.session_id, body.text)
                    retained = []
            except Exception as error:
                raise public_error(error) from None
            result = {'accepted': True, 'kind': body.kind, 'retained': retained}
            self.receipts[key] = (fingerprint, result)
            return result

    async def wait(self, run_id):
        await asyncio.shield(self.get(run_id).task)

    async def close(self):
        if self.active is not None and not self.active.task.done():
            try:
                self.application.request_stop(self.active.session_id, run_id=self.active.run_id)
            except Exception as error:
                logger.info('web_shutdown_stop_not_accepted error_type=%s', type(error).__name__)
            await asyncio.shield(self.active.task)
