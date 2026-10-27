"""Own HTTP jobs and bounded event replay; never execute an Agent loop here."""

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging

from langchain_core.messages import HumanMessage

from naiyue_test_agent.application.errors import RunCapacityExceededError
from naiyue_test_agent.agent_runtime.async_utils import await_settled
from .errors import WebError, public_error
from .serialization import message_view
from .delivered_run import DeliveredRun


logger = logging.getLogger(__name__)


class RunDelivery:
    def __init__(self, application, settings, *, progress_settings=None):
        self.application, self.settings = application, settings
        self.progress_settings = progress_settings
        self.runs = OrderedDict()
        self.latest = {}
        self.receipts = {}
        self.pending_receipts = {}
        self.start_lock = asyncio.Lock()
        self.control_lock = asyncio.Lock()
        self.active_by_session = {}
        self._closing = False

    def get(self, run_id):
        try:
            return self.runs[run_id]
        except KeyError:
            raise WebError('run_not_found', 404) from None

    def forget_session(self, session_id):
        identities = {key for key, job in self.runs.items() if job.session_id == session_id}
        for key in identities:
            self.runs.pop(key)
        self.latest.pop(session_id, None)
        for receipts in (self.receipts, self.pending_receipts):
            for key in tuple(receipts):
                if key[0] == session_id or key[0] in identities:
                    receipts.pop(key)
        self.active_by_session.pop(session_id, None)

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

    async def start(self, session_id, body, rows, *, can_run=True, conversation_revision=0, execution_root_error=None, prepared_message=None):
        return await self._launch(session_id, body, rows, can_run=can_run, conversation_revision=conversation_revision, execution_root_error=execution_root_error, prepared_message=prepared_message)

    async def regenerate(self, session_id, body, rows, *, can_run=True, execution_root_error=None):
        return await self._launch(session_id, body, rows, can_run=can_run, regenerate=True, execution_root_error=execution_root_error)

    async def compact(self, session_id, body, rows, *, can_run=True, conversation_revision=0, execution_root_error=None):
        return await self._launch(session_id, body, rows, can_run=can_run, compact=True, conversation_revision=conversation_revision, execution_root_error=execution_root_error)

    async def resume(self, session_id, body, check_current):
        return await self._launch(session_id, body, [], resume_check=check_current)

    async def _launch(self, session_id, body, rows, *, can_run=True, resume_check=None, compact=False, regenerate=False, conversation_revision=0, execution_root_error=None, prepared_message=None):
        async with self.start_lock:
            if self._closing:
                raise WebError('session_unavailable', 503)
            key, fingerprint, old = self._cached(session_id, body.request_id, {**body.model_dump(),
                '_operation': 'regenerate' if regenerate else 'compact' if compact else 'approval' if resume_check else 'message'})
            if old is not None:
                return old
            pending = self.pending_receipts.get(key)
            if pending is not None:
                if pending[0] != fingerprint:
                    raise WebError('request_id_conflict')
                job = pending[1]
            else:
                prepared = await resume_check() if resume_check is not None else None
                active = self.active_by_session.get(session_id)
                if prepared is not None and prepared['kind'] == 'live_child':
                    if (active is None or active.task.done() or active.run_id != body.origin_run_id):
                        raise WebError('stale_approval')
                    try:
                        receipt = await self.application.runs.submit_subagent_approval(session_id,
                            approval_batch_id=body.approval_batch_id, origin_run_id=body.origin_run_id,
                            decisions=[item.model_dump() for item in body.decisions], remember_approved=body.remember_approved)
                    except ValueError:
                        raise WebError('stale_approval') from None
                    result = {'session_id': session_id, **receipt, 'kind': 'live_child'}
                    self.receipts[key] = (fingerprint, result)
                    return result
                if active is not None and not active.task.done():
                    raise WebError('session_busy')
                if not can_run:
                    if execution_root_error is not None:
                        raise WebError('execution_root_unavailable', reason=execution_root_error['reason'])
                    raise WebError('session_recovery_blocked')
                def register(job):
                    self.runs[job.run_id] = job
                    self.latest[session_id] = job.run_id
                    self.receipts[key] = (fingerprint, {'session_id': session_id, 'run_id': job.run_id})
                    self.pending_receipts.pop(key, None)
                    self._prune()

                previous = self.runs.get(self.latest.get(session_id))
                states = {}
                if previous is not None and not regenerate:
                    rows, states = previous.seed_data()
                if prepared is not None:
                    present = {row['id'] for row in rows}
                    rows = [*rows, *(row for row in prepared['messages'] if row['id'] not in present)]
                job = DeliveredRun(session_id, rows, self.settings, register, progress_settings=self.progress_settings)
                job.conversation_revision = (body.expected_conversation_revision + 1 if regenerate else
                    (prepared or {}).get('conversation_revision', conversation_revision))
                job.tool_states = states
                job.unsubscribe = self.application.observability.subscribe(job.observe, session_id=session_id)
                self.pending_receipts[key] = (fingerprint, job)
                self.active_by_session[session_id] = job
                job.task = asyncio.create_task(self._produce(job, body.model_copy(update={'images': []}) if hasattr(body, 'images') else body, receipt_key=key, prepared=prepared, compact=compact, regenerate=regenerate, prepared_message=prepared_message))
        # Missing observations may keep this future pending for the whole run.
        # That must not hold the registration lock or cancel the producer.
        run_id = await asyncio.shield(job.started)
        return {'session_id': session_id, 'run_id': run_id}

    def _prune(self):
        finished = [key for key, job in self.runs.items() if job.done]
        for key in finished[:-self.settings.retained_runs]:
            old = self.runs.pop(key)
            for receipt in tuple(self.receipts):
                if receipt[0] == key:
                    self.receipts.pop(receipt)
            if self.latest.get(old.session_id) == key:
                self.latest.pop(old.session_id, None)

    async def _produce(self, job, body, *, receipt_key, prepared=None, compact=False, regenerate=False, prepared_message=None):
        try:
            if regenerate:
                result = await self.application.runs.regenerate(job.session_id, **body.model_dump())
            elif compact:
                result = await self.application.runs.compact(job.session_id)
                if job.compaction_result is None and result.get('compaction_status') is not None:
                    with job.lock:
                        job.compaction_result = {'status': result['compaction_status'], 'reason': None}
            elif prepared is not None:
                if prepared.get('stop'):
                    result = await self.application.runs.stop_approval(job.session_id,
                        approval_batch_id=body.approval_batch_id, origin_run_id=body.origin_run_id)
                else:
                    result = await self.application.runs.resume_approval(job.session_id, prepared['decisions'],
                        remember_approved=prepared['remember_approved'])
            else:
                result = await self.application.runs.start(job.session_id,
                    [prepared_message if prepared_message is not None else HumanMessage(content=body.text, id=body.request_id)],
                    **({'expected_conversation_revision': body.expected_conversation_revision}
                       if body.expected_conversation_revision is not None else {}))
            if job.status == 'running':
                with job.lock:
                    job.status = ('interrupted' if result.get('approval_requests') else
                                  'stopped' if result.get('run_stopped') is True else 'completed')
                    job.projection.incomplete = True
                    job.projection.seed(job.session_id, [message_view(m) for m in result.get('messages', [])])
                    job._mark_tools_unconfirmed(datetime.now(timezone.utc).isoformat())
        except BaseException as error:
            rejected = isinstance(error, RunCapacityExceededError) and job.run_id is None
            if rejected:
                pending = self.pending_receipts.get(receipt_key)
                if pending is not None and pending[1] is job:
                    self.pending_receipts.pop(receipt_key)
            if job.run_id is not None:
                job._ready(job.run_id)
            if not job.started.done():
                job.started.set_exception(public_error(error))
            if rejected:
                logger.info('web_run_admission_rejected reason=capacity')
            else:
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
            try:
                job.unsubscribe()
            except Exception as error:
                logger.error('web_event_unsubscribe_failed error_type=%s', type(error).__name__)
            with job.lock:
                job.done = True
                job._mark_tools_unconfirmed(datetime.now(timezone.utc).isoformat())
            if self.active_by_session.get(job.session_id) is job:
                self.active_by_session.pop(job.session_id)
            job.changed.set()

    async def control(self, run_id, body):
        prepared_message = None
        if body.images:
            # Only the paired queue transition holds control_lock. A stop must
            # remain usable while an image worker drains outside this lock.
            _, _, cached = self._cached(run_id, body.request_id, body.model_dump())
            if cached is not None:
                return cached
            initial = self.get(run_id)
            if self.active_by_session.get(initial.session_id) is not initial or initial.done or initial.status != 'running':
                raise WebError('stale_run')
            try:
                prepared_message = await self.application.runs.prepare_input(initial.session_id,
                    body.text or '', body.images, message_id=body.request_id)
            except Exception as error:
                raise public_error(error) from None
        async with self.control_lock:
            key, fingerprint, old = self._cached(run_id, body.request_id, body.model_dump())
            if old is not None:
                return old
            job = self.get(run_id)
            if self.active_by_session.get(job.session_id) is not job or job.done or job.status != 'running':
                raise WebError('stale_run')
            try:
                if body.kind == 'stop':
                    options = {key: value for key, value in (('agent_run_id', body.agent_run_id),
                        ('approval_batch_id', body.approval_batch_id)) if value is not None}
                    retained = self.application.runs.stop(job.session_id, run_id=run_id, agent_path=body.agent_path, **options)
                    if body.agent_path == 'root':
                        from collections import Counter
                        image_texts = Counter(row['legacy_text'] for row in self.application.runs.pending_inputs(job.session_id))
                        kept = []
                        for text in retained:
                            if image_texts[text]:
                                image_texts[text] -= 1
                            else:
                                kept.append(text)
                        retained = kept
                        with job.lock:
                            job.retained = list(retained)
                            if job.status == 'running':
                                job.stop_requested = True
                else:
                    method = self.application.runs.steer if body.kind == 'steering' else self.application.runs.follow_up
                    if body.images:
                        self.application.runs.queue_input(job.session_id,prepared_message,kind=body.kind,replace_ids=body.pending_input_ids)
                    else:
                        method(job.session_id, body.text)
                    retained = []
            except Exception as error:
                raise public_error(error) from None
            from .serialization import pending_input_views
            result = {'accepted': True, 'kind': body.kind, 'retained': retained,
                      'retained_inputs': pending_input_views(self.application.runs.pending_inputs(job.session_id), self.settings)}
            self.receipts[key] = (fingerprint, result)
            return result

    async def wait(self, run_id):
        await asyncio.shield(self.get(run_id).task)

    async def seal(self):
        async with self.start_lock:
            self._closing = True

    async def close(self, *, close_application=True):
        await self.seal()
        tasks = tuple(job.task for job in self.active_by_session.values())
        primary = None
        try:
            if close_application:
                await self.application.aclose()
        except BaseException as error:
            primary = error
        try:
            results = await await_settled(asyncio.gather(*tasks, return_exceptions=True))
            for result in results:
                if isinstance(result, BaseException):
                    logger.error('web_delivery_close_failed error_type=%s', type(result).__name__)
        except BaseException as error:
            if primary is None:
                raise
            logger.error('web_delivery_close_failed error_type=%s', type(error).__name__)
        if primary is not None:
            raise primary
