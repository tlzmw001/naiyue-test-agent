"""Observe invocations and close callback gaps without retaining messages."""

import asyncio
import logging
from threading import RLock

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.callbacks.base import BaseCallbackManager
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult, ChatGenerationChunk

from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.model_access import MODEL_ID_METADATA_KEY
from naiyue_test_agent.model_access.usage import normalize_model_usage
from .call_observation import event_label
from .events import ModelInvocationStartedData, ModelInvocationFinishedData
from .observation import emit_event
from .run_control import UserStop
from .run_services import current_run_services
from .stream_events import SUMMARY_TAG, PERMISSION_REVIEW_TAG


logger = logging.getLogger(__name__)


def _purpose(tags):
    known = {SUMMARY_TAG: 'summary', PERMISSION_REVIEW_TAG + '_fast': 'auto_fast',
             PERMISSION_REVIEW_TAG + '_detailed': 'auto_detailed'}
    selected = {known[tag] for tag in tags or [] if tag in known}
    if len(selected) == 1:
        return selected.pop()
    if selected or PERMISSION_REVIEW_TAG in (tags or []):
        logger.warning('model_usage_purpose_unavailable')
        return None
    return 'agent'


def _usage(response, *, failed=False):
    message = None
    groups = response.generations if isinstance(response, LLMResult) else []
    # astream errors carry [merged partial chunk, error-metadata group]. Only
    # the first group owns usage; never inspect error bodies or headers.
    partial_stream = (failed and len(groups) == 2 and len(groups[0]) == 1
                      and isinstance(groups[0][0], ChatGenerationChunk))
    if len(groups) == 1 or partial_stream:
        choices = groups[0]
        if choices:
            message = choices[0].message if hasattr(choices[0], 'message') else None
    return normalize_model_usage(message.usage_metadata if isinstance(message, AIMessage) else None)


class ModelUsageCallback(AsyncCallbackHandler):
    run_inline = True

    def __init__(self):
        self._active = {}
        self._lock = RLock()

    async def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, tags=None, **kwargs):
        services = current_run_services()
        if services is None or services.publisher is None:
            return
        try:
            model_id = event_label((metadata or {}).get(MODEL_ID_METADATA_KEY))
            if model_id is None:
                logger.warning('model_usage_identity_unavailable')
            data = ModelInvocationStartedData(str(run_id), model_id, _purpose(tags))
            owner = asyncio.current_task()
            done = lambda task: self._task_done(data.invocation_id, task)
            with self._lock:
                if data.invocation_id in self._active:
                    logger.warning('model_usage_duplicate_start')
                    return
                self._active[data.invocation_id] = (services.publisher, owner, done)
            if owner is not None:
                owner.add_done_callback(done)
            emit_event(services.publisher, 'model_invocation_started', data)
        except Exception as error:
            logger.error('model_usage_observation_failed error_type=%s', safe_error_type(error))

    async def on_llm_end(self, response, *, run_id, **kwargs):
        self._finish(run_id, 'returned', response)

    async def on_llm_error(self, error, *, run_id, **kwargs):
        status = 'cancelled' if isinstance(error, (asyncio.CancelledError, UserStop)) else 'failed'
        self._finish(run_id, status, kwargs.get('response'), error)

    def _finish(self, run_id, status, response, error=None):
        with self._lock:
            record = self._active.pop(str(run_id), None)
        if record is None:
            return
        publisher, owner, done = record
        if owner is not None:
            owner.remove_done_callback(done)
        try:
            usage = _usage(response, failed=status != 'returned')
            reported = any(value is not None for value in usage.values())
            usage_status = ('final' if status == 'returned' else 'partial') if reported else 'unavailable'
            emit_event(publisher, 'model_invocation_finished', lambda: ModelInvocationFinishedData(
                str(run_id), status, usage_status, **usage,
                error_type=safe_error_type(error) if error is not None else None))
        except Exception as fault:
            logger.error('model_usage_observation_failed error_type=%s', safe_error_type(fault))

    def _task_done(self, identity, task):
        if task.cancelled():
            self._finish(identity, 'cancelled', None, asyncio.CancelledError())
        elif task.exception() is not None:
            error = task.exception()
            status = 'cancelled' if isinstance(error, UserStop) else 'failed'
            self._finish(identity, status, None, error)


def install_model_usage_callback(catalog):
    """App startup only; preserve existing callbacks and shared model ownership."""
    callback, seen = ModelUsageCallback(), set()
    for identity in catalog.model_ids:
        model = catalog.get(identity)
        if id(model) in seen:
            continue
        seen.add(id(model))
        existing = model.callbacks
        handlers = existing.handlers if isinstance(existing, BaseCallbackManager) else existing or []
        if any(isinstance(handler, ModelUsageCallback) for handler in handlers):
            continue
        if isinstance(existing, BaseCallbackManager):
            model.callbacks = existing.copy()
            model.callbacks.add_handler(callback, inherit=False)
        else:
            model.callbacks = [*handlers, callback]
