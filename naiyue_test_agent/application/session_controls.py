"""Session routing and the owning object's control-state operations."""

from naiyue_test_agent.agent_runtime.observation import observe, emit_event
from naiyue_test_agent.contracts.session_events import ControlData

import logging
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import HumanMessage, convert_to_messages

from .errors import SessionBusyError, SessionPersistenceError
from .session_state import SessionState
from naiyue_test_agent.tools.read import resolve_workspace_root


logger = logging.getLogger('naiyue_test_agent.application')


def mark_human_messages(messages):
    result = []
    for message in convert_to_messages(messages):
        extra = {key: value for key, value in message.additional_kwargs.items() if key != 'naiyue_human_input'}
        if isinstance(message, HumanMessage) and not extra.get('naiyue_compaction_summary'):
            extra['naiyue_human_input'] = True
        result.append(message.model_copy(update={'additional_kwargs': extra}))
    return result


class AgentSessionControls:
    """The owning session pairs its queues with its current run under one lock."""

    def _current_control(self):
        # Caller holds the session state lock; this only borrows the reference.
        return self._run.observation.controls if self._run is not None and self._run.controls_open else None

    def steer(self, text):
        with self._lock:
            row = self._run
            if row is None or not row.steering_open:
                raise SessionBusyError('Session is not accepting steering messages')
            control, run_id = self._current_control(), row.run_id
            size = len(row.steering_queue)
            row.steering_queue.append(text)
            if control:
                observe(control, control.accept, 'steering', queue_size=size)
        self._record_steering('steering_accepted', self.session_id, run_id, 1)
        if control:
            observe(control, control.flush)

    def follow_up(self, text):
        with self._lock:
            row = self._run
            if row is None or not row.steering_open:
                raise SessionBusyError('Session is not accepting follow-up messages')
            control, run_id = self._current_control(), row.run_id
            size = len(row.follow_up_queue)
            row.follow_up_queue.append(text)
            if control:
                observe(control, control.accept, 'follow_up', queue_size=size)
        self._record_follow_up('follow_up_accepted', self.session_id, run_id, 1)
        if control:
            observe(control, control.flush)

    def request_scope_stop(self, *, run_id=None, agent_path='root', reason='user'):
        targets = []
        with self._lock:
            row = self._run
            active = row.observation if row is not None and row.controls_open else None
            if row is None:
                if reason != 'user':
                    return []
                raise SessionBusyError('Session has no active run to stop')
            if run_id is not None and run_id != row.run_id:
                raise SessionBusyError('Stop request targets a different run')
            retained, control, stop_control = [], None, None
            if agent_path == 'root':
                if row.stop_requested:
                    return []
                if reason == 'user' and ((not row.steering_open and (active is None or active.mode != 'compact'))
                                        or (active is not None and active.stage == 'finalize')):
                    raise SessionBusyError('Session is no longer accepting stop requests')
                retained = [*row.steering_queue, *row.follow_up_queue]
                row.stop_requested, row.steering_open = True, False
                if active is not None:
                    stop_control = active.stop_control
                    stop_control.mark_requested(reason)
                control = active.controls if active is not None and reason == 'user' else None
                if control:
                    observe(control, control.accept, 'stop')
            run_id = row.run_id
            if agent_path != 'root':
                instance = row.subagent_instances.get(agent_path)
                if (instance is None or instance['root_run_id'] != run_id
                        or instance['phase'] == 'finalize' or instance['settled']):
                    if reason != 'user':
                        return []
                    raise SessionBusyError('Subagent is no longer accepting stop requests')
                candidates = [instance]
            else:
                candidates = [instance for instance in row.subagent_instances.values()
                              if instance['root_run_id'] == run_id]
            for instance in candidates:
                if instance['phase'] == 'finalize' or instance['status'] == 'stopping':
                    continue
                instance['status'] = 'stopping'
                instance['control'].mark_requested(reason)
                pending = instance.get('approval')
                if pending is not None and pending.status == 'pending':
                    pending.status = 'invalidated'
                targets.append((instance, pending))
        if stop_control is not None:
            stop_control.request(reason)
        for instance, pending in targets:
            instance['control'].request(reason)
            if reason == 'user':
                emit_event(instance.get('publisher_view'), 'control_accepted',
                    lambda: ControlData(uuid4().hex, 'stop'))
            if pending is not None and pending.status == 'invalidated':
                def wake(future=pending.future):
                    if not future.done():
                        future.set_result(([], None))
                pending.future.get_loop().call_soon_threadsafe(wake)
        logger.info('stop_requested', extra={'stop_event': {
            'event': 'stop_requested', 'session_id': self.session_id, 'run_id': run_id,
            'agent_path': agent_path, 'reason': reason,
        }})
        if control:
            observe(control, control.flush)
        return retained

    def is_stop_requested(self):
        with self._lock:
            return self._run.stop_requested if self._run else False

    def drain_steering(self):
        with self._lock:
            row = self._run
            if row is None:
                return []
            control, run_id = self._current_control(), row.run_id
            messages = list(row.steering_queue)
            row.steering_queue.clear()
            if control and messages:
                observe(control, control.select, 'steering', len(messages))
        if messages:
            self._record_steering('steering_injected', self.session_id, run_id, len(messages))
        if control:
            observe(control, control.flush)
        return messages

    def finish_run_boundary(self):
        with self._lock:
            row = self._run
            if row is None:
                return False, []
            if row.stop_requested:
                row.steering_open = False
                return True, []
            if row.steering_queue:
                messages, kind = list(row.steering_queue), 'steering'
                row.steering_queue.clear()
            elif row.follow_up_queue:
                messages, kind = [row.follow_up_queue.popleft()], 'follow_up'
            else:
                row.steering_open = False
                return False, []
            control, run_id = self._current_control(), row.run_id
            if control:
                observe(control, control.select, kind, len(messages))
        if kind == 'steering':
            self._record_steering('steering_injected', self.session_id, run_id, len(messages))
        else:
            self._record_follow_up('follow_up_injected', self.session_id, run_id, len(messages))
        if control:
            observe(control, control.flush)
        return False, messages

    def prepare_control_messages(self, messages):
        with self._lock:
            control = self._current_control()
        prepared = (observe(control, control.prepare_messages, messages,
            fallback=[HumanMessage(content=text) for text in messages]) if control is not None
            else [HumanMessage(content=text) for text in messages])
        return mark_human_messages(prepared)

    @staticmethod
    def _record_steering(event: str, session_id: str, run_id: str, count: int) -> None:
        logger.info(event, extra={'steering_event': {
            'event': event, 'session_id': session_id, 'run_id': run_id, 'count': count,
        }})

    @staticmethod
    def _record_follow_up(event: str, session_id: str, run_id: str, count: int) -> None:
        logger.info(event, extra={'follow_up_event': {
            'event': event, 'session_id': session_id, 'run_id': run_id, 'count': count,
        }})


class SessionControlMixin:
    """Multi-session registry/routing; instance State belongs to AgentSession."""

    def _session(self, session_id):
        with self._lock:
            return self._sessions[session_id]

    def create_session(self, model_id: str) -> str:
        return self._create_session(model_id, None)

    def create_workspace_session(self, model_id: str, workspace_root: str | Path) -> str:
        return self._create_session(model_id, resolve_workspace_root(workspace_root))

    def _create_session(self, model_id: str, workspace_root: Path | None) -> str:
        from .agent_session import AgentSession
        self._catalog.get(model_id)
        with self._lock:
            session_id = uuid4().hex
            while session_id in self._sessions:
                session_id = uuid4().hex
            state = SessionState(thread_id=uuid4().hex, model_id=model_id,
                workspace_root=workspace_root, active_tool_names=self._initial_active_tools,
                permission_mode=self._permission_settings.mode)
            session = AgentSession(session_id, state, catalog=self._catalog,
                tool_registry=self._tool_registry, permission_settings=self._permission_settings,
                persistence=self._persistence, invoke_subagent=self.ainvoke_subagent,
                registered_subagents=self.get_registered_subagents, progress_settings=self._progress_settings,
                subagent_result_settings=getattr(self, '_subagent_result_settings', None))
            self._sessions[session_id] = session
        try:
            if self._persistence is not None:
                self._persistence.storage.create(session_id, {
                    'thread_id': session.get_thread_id(), 'model_id': model_id,
                    'workspace_root': workspace_root, 'active_tool_names': session.get_active_tools(),
                    'permission_mode': session.get_permission_mode()})
        except BaseException:
            with self._lock:
                self._sessions.pop(session_id, None)
            raise
        return session_id

    def has_session(self, session_id):
        with self._lock:
            return session_id in self._sessions

    def describe_session(self, session_id):
        return self._session(session_id).describe_session()

    def get_thread_id(self, session_id):
        return self._session(session_id).get_thread_id()

    def loaded_session_ids(self):
        with self._lock:
            return tuple(self._sessions)

    def snapshot_for_context(self, session_id):
        return self._session(session_id).snapshot_for_context()

    def context_snapshot_is_current(self, session_id, snapshot):
        with self._lock:
            session = self._sessions.get(session_id)
            return session is not None and session.context_snapshot_is_current(snapshot)

    def approval_seed_identity(self, session_id):
        return self._session(session_id).approval_seed_identity()

    def install_restored_session(self, session_id, *, thread_id, model_id, thinking_level,
                                 workspace_root, active_tool_names, permission_mode, pending=None):
        from .agent_session import AgentSession
        with self._lock:
            if session_id in self._sessions:
                raise SessionPersistenceError('session_already_loaded')
            state = SessionState(thread_id=thread_id, model_id=model_id,
                thinking_level=thinking_level, workspace_root=workspace_root,
                active_tool_names=tuple(active_tool_names), permission_mode=permission_mode,
                pending_tool_approval=deepcopy(pending))
            self._sessions[session_id] = AgentSession(session_id, state, catalog=self._catalog,
                tool_registry=self._tool_registry, permission_settings=self._permission_settings,
                persistence=self._persistence, invoke_subagent=self.ainvoke_subagent,
                registered_subagents=self.get_registered_subagents, progress_settings=self._progress_settings,
                subagent_result_settings=getattr(self, '_subagent_result_settings', None))

    @contextmanager
    def deletion_guard(self, session_id):
        # Caller owns persistence's lock; reserve deletion before releasing this guard.
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and session.is_busy():
                raise SessionPersistenceError('session_recovery_blocked')
            yield

    def child_thread_ids(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
            return session.child_thread_ids() if session is not None else ()

    def remove_session(self, session_id):
        with self._lock:
            self._sessions.pop(session_id, None)

    def select_thinking(self, session_id, model_id, level):
        self._session(session_id).select_thinking(model_id, level)

    def register_tool(self, spec):
        self._tool_registry.register(spec, permission_settings=self._permission_settings)

    def get_permission_mode(self, session_id):
        return self._session(session_id).get_permission_mode()

    def set_permission_mode(self, session_id, mode):
        self._session(session_id).set_permission_mode(mode)

    def resolve_permission_context(self, session_id, agent_path='root'):
        return self._session(session_id).resolve_permission_context(agent_path)

    def get_active_tools(self, session_id):
        return self._session(session_id).get_active_tools()

    def resolve_active_tool_names(self, session_id, agent_path='root'):
        return self._session(session_id).get_active_tools(agent_path)

    def set_active_tools(self, session_id, names):
        self._session(session_id).set_active_tools(names)

    def steer(self, session_id: str, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty steering message is required')
        self._session(session_id).steer(text)

    def follow_up(self, session_id: str, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty follow-up message is required')
        self._session(session_id).follow_up(text)

    def request_stop(self, session_id: str, *, run_id: str | None = None, agent_path='root') -> list[str]:
        return self._session(session_id).request_scope_stop(run_id=run_id, agent_path=agent_path)

    def is_stop_requested(self, session_id: str) -> bool:
        return self._session(session_id).is_stop_requested()

    def drain_steering(self, session_id: str) -> list[str]:
        return self._session(session_id).drain_steering()

    def finish_run_boundary(self, session_id: str) -> tuple[bool, list[str]]:
        return self._session(session_id).finish_run_boundary()

    def prepare_control_messages(self, session_id: str, messages: list[str]) -> list[HumanMessage]:
        return self._session(session_id).prepare_control_messages(messages)
