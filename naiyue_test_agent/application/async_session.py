"""Async execution of the session service's shared admission and cleanup rules."""

import logging

from .io_steps import IOCall, run_steps, arun_steps
from langchain_core.messages import AIMessage, ToolMessage
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.agent_runtime.events import safe_error_type


logger = logging.getLogger(__name__)


class AsyncSessionMixin:
    def _review_partial_state(self, agent, session_id):
        return run_steps(self._partial_review_steps(agent, session_id))

    async def _areview_partial_state(self, agent, session_id):
        return await arun_steps(self._partial_review_steps(agent, session_id))

    def _partial_review_steps(self, agent, session_id):
        with self._lock:
            thread = self._sessions[session_id]['thread_id']
        try:
            snapshot = yield IOCall(agent.get_state, ({'configurable': {'thread_id': thread}},),
                                    async_call=agent.aget_state)
        except BaseException as error:
            logger.error('partial_state_review_failed error_type=%s', safe_error_type(error))
            return True
        messages = snapshot.values.get('messages', [])
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], AIMessage):
                calls = {call.get('id') for call in messages[index].tool_calls}
                results = {m.tool_call_id for m in messages[index + 1:] if isinstance(m, ToolMessage)}
                return bool(calls - results)
        return False

    async def arun(self, agent, session_id, messages, *, run_id=None, max_concurrency=4):
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError('Tool concurrency must be a positive integer')
        return await arun_steps(self._run_steps(agent, session_id, messages,
            run_id=run_id, async_mode=True, max_concurrency=max_concurrency))

    async def aresume_tool_approval(self, agent, session_id, decisions, *, run_id=None, max_concurrency=4):
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError('Tool concurrency must be a positive integer')
        return await arun_steps(self._resume_steps(agent, session_id, decisions,
            run_id=run_id, async_mode=True, max_concurrency=max_concurrency))

    async def _afinish_run(self, session_id, run_id, session, agent, observation, primary_error):
        secondary = None
        review_required = session['manual_review_required']
        try:
            review_required = self._cleanup_run(session_id, run_id, session, observation)
        except Exception as error:
            if primary_error is None:
                secondary = error
                observation.failed(error, stage='cleanup')
            else:
                logger.error('session_cleanup_failed error_type=%s', safe_error_type(error))
        try:
            if self._persistence is not None:
                await self._persistence.afinish(session_id, run_id, agent, observation)
        except Exception as error:
            if primary_error is None and secondary is None:
                secondary = error
                observation.failed(error, stage='finalize')
            else:
                logger.error('session_persistence_secondary_failed error_type=%s', safe_error_type(error))
        finally:
            if self._persistence is not None:
                with self._lock:
                    session['busy'] = False
                    session['run_id'] = None
                    session['steering_open'] = False
                    session['stop_requested'] = False
                    session['run_project_instructions'] = None
                    session['steering_queue'].clear()
                    session['follow_up_queue'].clear()
                    self._active_runs.pop(session_id, None)
        try:
            observation.finish(review_required)
        except Exception as error:
            if primary_error is None and secondary is None and observation.failure is None:
                raise
            logger.error('run_event_publish_failed error_type=%s', safe_error_type(error))
        if secondary is not None:
            raise secondary
