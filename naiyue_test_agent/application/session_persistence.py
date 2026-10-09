"""Critical persistence boundaries, separate from optional event consumers."""

import logging
from collections import deque
from threading import Lock

from .session_storage import SessionPersistenceError
from .io_steps import IOCall, run_steps, arun_steps
from naiyue_test_agent.agent_runtime.tools.read import resolve_workspace_root


logger = logging.getLogger(__name__)


class SessionPersistence:
    def __init__(self, storage, sessions):
        self.storage = storage
        self.sessions = sessions
        self._lock = Lock()
        self._active = None
        self._blocked = set()

    def start(self, session_id, run_id):
        with self._lock:
            if self._active is not None or session_id in self._blocked:
                raise SessionPersistenceError('session_recovery_blocked')
            self.storage.mark_running(session_id, run_id)
            self._active = (session_id, run_id)

    def finish(self, session_id, run_id, agent, observation):
        return run_steps(self._finish_steps(session_id, run_id, agent, observation))

    async def afinish(self, session_id, run_id, agent, observation):
        return await arun_steps(self._finish_steps(session_id, run_id, agent, observation))

    def _finish_steps(self, session_id, run_id, agent, observation):
        with self._lock:
            if self._active != (session_id, run_id):
                return
        try:
            with self.sessions._lock:
                session = dict(self.sessions._sessions[session_id])
            status = observation.status
            state = ('review_required' if session['manual_review_required'] else
                     'interrupted' if status == 'interrupted' else 'failed' if status == 'failed' else 'ready')
            previous = yield IOCall(self.storage.get, (session_id,))
            checkpoint_id, namespace = previous['checkpoint_id'], previous['checkpoint_ns']
            if state == 'ready':
                snapshot = yield IOCall(agent.get_state, ({'configurable': {'thread_id': session['thread_id']}},),
                                        async_call=agent.aget_state)
                checkpoint_id = snapshot.config.get('configurable', {}).get('checkpoint_id')
                namespace = snapshot.config.get('configurable', {}).get('checkpoint_ns', '')
                if (not checkpoint_id or snapshot.next or
                        any(task.interrupts or task.error for task in snapshot.tasks)):
                    raise SessionPersistenceError('checkpoint_not_safely_finished')
            yield IOCall(self.storage.finish_run, (session_id, state, checkpoint_id, namespace, status))
        except Exception as error:
            with self._lock:
                self._blocked.add(session_id)
            logger.error('session_persistence_finalize_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_save_unconfirmed') from error
        finally:
            with self._lock:
                self._active = None

    def restore(self, session_id, agent):
        return run_steps(self._restore_steps(session_id, agent))

    async def arestore(self, session_id, agent):
        return await arun_steps(self._restore_steps(session_id, agent))

    def _restore_steps(self, session_id, agent):
        row = yield IOCall(self.storage.get, (session_id,))
        if row['state'] != 'ready':
            raise SessionPersistenceError('session_recovery_blocked')
        self.sessions._catalog.get(row['model_id'])
        root = (yield IOCall(resolve_workspace_root, (row['workspace_root'],))) if row['workspace_root'] else None
        snapshot = yield IOCall(agent.get_state, ({'configurable': {'thread_id': row['thread_id']}},),
                                async_call=agent.aget_state)
        actual = snapshot.config.get('configurable', {}).get('checkpoint_id')
        namespace = snapshot.config.get('configurable', {}).get('checkpoint_ns', '')
        if (row['checkpoint_id'] != actual or row['checkpoint_ns'] != namespace or snapshot.next
                or any(task.interrupts or task.error for task in snapshot.tasks)):
            raise SessionPersistenceError('checkpoint_mismatch')
        with self.sessions._lock:
            if session_id in self.sessions._sessions:
                raise SessionPersistenceError('session_already_loaded')
            self.sessions._sessions[session_id] = {
                'thread_id': row['thread_id'], 'model_id': row['model_id'], 'workspace_root': root,
                'pending_tool_approval': None, 'pending_turn': None, 'manual_review_required': False,
                'busy': False, 'run_id': None, 'run_project_instructions': None,
                'steering_open': False, 'stop_requested': False,
                'steering_queue': deque(), 'follow_up_queue': deque(),
            }
        return session_id
