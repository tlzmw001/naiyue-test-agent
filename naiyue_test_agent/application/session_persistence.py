"""Critical persistence boundaries, separate from optional event consumers."""

import logging
import json
from collections import deque
from threading import Lock

from .session_storage import SessionPersistenceError
from .io_steps import IOCall, arun_steps
from .session_recovery import history_steps, recovery_steps
from naiyue_test_agent.tools.read import resolve_workspace_root
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from .subagent_history import subagent_records


logger = logging.getLogger(__name__)


class SessionPersistence:
    def __init__(self, storage, sessions):
        self.storage = storage
        self.sessions = sessions
        self._lock = Lock()
        self._active = None
        self._blocked = set()
        self._recovering = None

    def start(self, session_id, run_id):
        with self._lock:
            if self._active is not None or self._recovering is not None or session_id in self._blocked:
                raise SessionPersistenceError('session_recovery_blocked')
            self.storage.mark_running(session_id, run_id)
            self._active = (session_id, run_id)

    async def adelete(self, session_id, agent):
        with self._lock, self.sessions._lock:
            if (self._active is not None or self._recovering is not None
                    or self.sessions._sessions.get(session_id, {}).get('busy')):
                raise SessionPersistenceError('session_recovery_blocked')
            self._recovering = session_id
        try:
            row = await run_blocking(self.storage.get, session_id)
            children = set()
            async for snapshot in agent.aget_state_history({'configurable': {'thread_id': row['thread_id']}}):
                children.update(f'subagent:{item["agent_id"]}' for item in subagent_records(session_id, snapshot.values.get('messages', [])))
            with self.sessions._lock:
                children.update(instance['child_thread_id'] for (sid, _), instance in self.sessions._subagent_instances.items() if sid == session_id)
            await run_blocking(self.storage.delete, session_id, children)
            with self._lock, self.sessions._lock:
                self.sessions._sessions.pop(session_id, None)
                for key in tuple(self.sessions._subagent_instances):
                    if key[0] == session_id:
                        self.sessions._subagent_instances.pop(key)
                self._blocked.discard(session_id)
        except Exception as error:
            logger.error('session_delete_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_delete_unconfirmed') from error
        finally:
            with self._lock:
                self._recovering = None


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
                snapshot = yield IOCall(args=({'configurable': {'thread_id': session['thread_id']}},),
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



    async def ahistory(self, session_id, agent):
        return await arun_steps(history_steps(self, session_id, agent))


    async def arecover(self, session_id, agent, *, checkpoint_id, action):
        return await arun_steps(self._recover_steps(session_id, agent, checkpoint_id, action))

    def _recover_steps(self, session_id, agent, checkpoint_id, action):
        with self._lock:
            if self._active is not None or self._recovering is not None or session_id in self._blocked:
                raise SessionPersistenceError('session_recovery_blocked')
            self._recovering = session_id
        try:
            if self.sessions.has_session(session_id):
                raise SessionPersistenceError('session_recovery_blocked')
            return (yield from recovery_steps(self, session_id, agent, checkpoint_id, action))
        finally:
            with self._lock:
                self._recovering = None

    async def arestore(self, session_id, agent):
        return await arun_steps(self._restore_steps(session_id, agent))

    def _restore_steps(self, session_id, agent):
        with self._lock:
            if self._recovering is not None:
                raise SessionPersistenceError('session_recovery_blocked')
            self._recovering = session_id
        try:
            row = yield IOCall(self.storage.get, (session_id,))
            if row['state'] != 'ready':
                raise SessionPersistenceError('session_recovery_blocked')
            root, names = yield from self._configuration_steps(row)
            snapshot = yield IOCall(args=({'configurable': {'thread_id': row['thread_id']}},),
                                    async_call=agent.aget_state)
            actual = snapshot.config.get('configurable', {}).get('checkpoint_id')
            namespace = snapshot.config.get('configurable', {}).get('checkpoint_ns', '')
            if (row['checkpoint_id'] != actual or row['checkpoint_ns'] != namespace or snapshot.next
                    or any(task.interrupts or task.error for task in snapshot.tasks)):
                raise SessionPersistenceError('checkpoint_mismatch')
            self._install_session(row, root, names, recovering=True)
            return session_id
        finally:
            with self._lock:
                self._recovering = None

    def _configuration_steps(self, row):
        try:
            self.sessions._permission_settings.validate_mode(row['permission_mode'])
        except ValueError as error:
            raise SessionPersistenceError('invalid_permission_mode') from error
        saved = row['active_tool_names_json']
        try:
            names = json.loads(saved)
            specs = self.sessions._tool_registry.select(names)
            names = tuple(spec.tool.name for spec in specs)
        except (ValueError, TypeError) as error:
            raise SessionPersistenceError('invalid_active_tools') from error
        self.sessions._catalog.with_thinking(row['model_id'], row['thinking_level'])
        root = (yield IOCall(resolve_workspace_root, (row['workspace_root'],))) if row['workspace_root'] else None
        return root, names

    def _install_session(self, row, root, names, *, recovering=False, pending=None):
        session_id = row['session_id']
        with self._lock, self.sessions._lock:
            if self._recovering is not None and not (recovering and self._recovering == session_id):
                raise SessionPersistenceError('session_recovery_blocked')
            if session_id in self.sessions._sessions:
                raise SessionPersistenceError('session_already_loaded')
            self.sessions._sessions[session_id] = {
                'thread_id': row['thread_id'], 'model_id': row['model_id'], 'workspace_root': root,
                'thinking_level': row['thinking_level'],
                'active_tool_names': names,
                'permission_mode': row['permission_mode'], 'session_allow': [],
                'pending_tool_approval': pending, 'pending_turn': None, 'manual_review_required': False,
                'busy': False, 'run_id': None,
                'run_review_project_instructions': None, 'run_prompt_parts': [],
                'steering_open': False, 'stop_requested': False,
                'steering_queue': deque(), 'follow_up_queue': deque(),
            }
