"""Session queue controls; the session service owns storage, lock, and errors."""

from naiyue_test_agent.agent_runtime.observation import observe, emit_event
from naiyue_test_agent.contracts.session_events import ControlData

import logging
from uuid import uuid4

from langchain_core.messages import HumanMessage, convert_to_messages


logger = logging.getLogger('naiyue_test_agent.application')


class SessionControlMixin:
    """Internal split for the 500-line limit; public control signatures remain."""

    def select_thinking(self, session_id, model_id, level):
        self._catalog.with_thinking(model_id, level)
        with self._model_selection_lock:
            with self._lock:
                if self._sessions[session_id]['model_id'] != model_id:
                    raise ValueError('Model selection changed')
            if self._persistence is not None:
                self._persistence.storage.select_thinking(session_id, level)
            with self._lock:
                self._sessions[session_id]['thinking_level'] = level

    def register_tool(self, spec):
        self._tool_registry.register(spec, permission_settings=self._permission_settings)

    def get_permission_mode(self, session_id):
        with self._lock:
            return self._sessions[session_id]['permission_mode']

    def set_permission_mode(self, session_id, mode):
        self._permission_settings.validate_mode(mode)
        with self._permission_selection_lock:
            with self._lock:
                self._sessions[session_id]
            if self._persistence is not None:
                self._persistence.storage.select_permission_mode(session_id, mode)
            with self._lock:
                self._sessions[session_id]['permission_mode'] = mode

    def resolve_permission_context(self, session_id, agent_path='root'):
        from copy import deepcopy
        with self._lock:
            if agent_path != 'root':
                self.subagent_instance(session_id, agent_path)
            row = self._sessions[session_id]
            return {'mode': row['permission_mode'], 'session_allow': deepcopy(row['session_allow']),
                    'project_instructions': row['run_review_project_instructions']}

    def get_active_tools(self, session_id):
        with self._lock:
            return tuple(self._sessions[session_id]['active_tool_names'])

    def resolve_active_tool_names(self, session_id, agent_path='root'):
        if agent_path != 'root':
            with self._lock:
                return self.subagent_instance(session_id, agent_path)['tool_names']
        return self.get_active_tools(session_id)

    def set_active_tools(self, session_id, names):
        selected = self._tool_registry.select(names)
        names = tuple(spec.tool.name for spec in selected)
        with self._tool_selection_lock:
            with self._lock:
                self._sessions[session_id]
            if self._persistence is not None:
                self._persistence.storage.select_active_tools(session_id, names)
            with self._lock:
                self._sessions[session_id]['active_tool_names'] = names

    def _control(self, session_id):
        observation = self._active_runs.get(session_id)
        return observation.controls if observation is not None else None

    def steer(self, session_id: str, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty steering message is required')
        with self._lock:
            session = self._sessions[session_id]
            if not session['busy'] or not session['steering_open']:
                raise self._busy_error('Session is not accepting steering messages')
            size = len(session['steering_queue'])
            session['steering_queue'].append(text)
            run_id = session['run_id']
            control = self._control(session_id)
            if control:
                observe(control, control.accept, 'steering', queue_size=size)
        self._record_steering('steering_accepted', session_id, run_id, 1)
        if control:
            observe(control, control.flush)

    def follow_up(self, session_id: str, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError('A non-empty follow-up message is required')
        with self._lock:
            session = self._sessions[session_id]
            if not session['busy'] or not session['steering_open']:
                raise self._busy_error('Session is not accepting follow-up messages')
            size = len(session['follow_up_queue'])
            session['follow_up_queue'].append(text)
            run_id = session['run_id']
            control = self._control(session_id)
            if control:
                observe(control, control.accept, 'follow_up', queue_size=size)
        self._record_follow_up('follow_up_accepted', session_id, run_id, 1)
        if control:
            observe(control, control.flush)

    def request_stop(self, session_id: str, *, run_id: str | None = None, agent_path='root') -> list[str]:
        return self._request_scope_stop(session_id, run_id=run_id, agent_path=agent_path)

    def _request_scope_stop(self, session_id, *, run_id=None, agent_path='root', reason='user'):
        targets = []
        with self._lock:
            session = self._sessions[session_id]
            if not session['busy']:
                if reason != 'user':
                    return []
                raise self._busy_error('Session has no active run to stop')
            if run_id is not None and run_id != session['run_id']:
                raise self._busy_error('Stop request targets a different run')
            active = self._active_runs.get(session_id)
            if agent_path != 'root':
                instance = self._subagent_instances.get((session_id, agent_path))
                if (instance is None or instance['root_run_id'] != session['run_id']
                        or instance['phase'] == 'finalize' or instance['settled']):
                    if reason != 'user':
                        return []
                    raise self._busy_error('Subagent is no longer accepting stop requests')
                candidates = [instance]
                retained, control = [], None
            else:
                if session['stop_requested']:
                    return []
                if reason == 'user' and ((not session['steering_open'] and (active is None or active.mode != 'compact'))
                                        or (active is not None and active.stage == 'finalize')):
                    raise self._busy_error('Session is no longer accepting stop requests')
                candidates = [instance for (sid, _), instance in self._subagent_instances.items()
                              if sid == session_id and instance['root_run_id'] == session['run_id']]
                retained = [*session['steering_queue'], *session['follow_up_queue']]
                session['stop_requested'] = True
                session['steering_open'] = False
                if active is not None:
                    active.stop_control.mark_requested(reason)
                control = self._control(session_id) if reason == 'user' else None
                if control:
                    observe(control, control.accept, 'stop')
            for instance in candidates:
                if instance['phase'] == 'finalize' or instance['status'] == 'stopping':
                    continue
                instance['status'] = 'stopping'
                instance['control'].mark_requested(reason)
                pending = instance.get('approval')
                if pending is not None and pending.status == 'pending':
                    pending.status = 'invalidated'
                targets.append((instance, pending))
            run_id = session['run_id']
        if agent_path == 'root' and active is not None:
            active.stop_control.request(reason)
        for instance, pending in targets:
            instance['control'].request(reason)
            publisher = instance.get('publisher_view')
            if reason == 'user':
                emit_event(publisher, 'control_accepted', lambda: ControlData(uuid4().hex, 'stop'))
            if pending is not None and pending.status == 'invalidated':
                def wake(future=pending.future):
                    if not future.done():
                        future.set_result(([], None))
                pending.future.get_loop().call_soon_threadsafe(wake)
        logger.info('stop_requested', extra={'stop_event': {
            'event': 'stop_requested', 'session_id': session_id, 'run_id': run_id,
            'agent_path': agent_path, 'reason': reason,
        }})
        if control:
            observe(control, control.flush)
        return retained

    def is_stop_requested(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions[session_id]['stop_requested']

    def _select_controls(self, session_id, kind, messages):
        control = self._control(session_id)
        if control and messages:
            observe(control, control.select, kind, len(messages))
        return control

    def drain_steering(self, session_id: str) -> list[str]:
        with self._lock:
            session = self._sessions[session_id]
            messages = list(session['steering_queue'])
            session['steering_queue'].clear()
            run_id = session['run_id']
            control = self._select_controls(session_id, 'steering', messages)
        if messages:
            self._record_steering('steering_injected', session_id, run_id, len(messages))
        if control:
            observe(control, control.flush)
        return messages


    def finish_run_boundary(self, session_id: str) -> tuple[bool, list[str]]:
        """Atomically choose stop, steering, follow-up, or natural completion."""
        with self._lock:
            session = self._sessions[session_id]
            if session['stop_requested']:
                session['steering_open'] = False
                return True, []
            if session['steering_queue']:
                messages = list(session['steering_queue'])
                session['steering_queue'].clear()
                kind = 'steering'
            elif session['follow_up_queue']:
                messages = [session['follow_up_queue'].popleft()]
                kind = 'follow_up'
            else:
                session['steering_open'] = False
                return False, []
            run_id = session['run_id']
            control = self._select_controls(session_id, kind, messages)
        if kind == 'steering':
            self._record_steering('steering_injected', session_id, run_id, len(messages))
        else:
            self._record_follow_up('follow_up_injected', session_id, run_id, 1)
        if control:
            observe(control, control.flush)
        return False, messages

    def prepare_control_messages(self, session_id: str, messages: list[str]) -> list[HumanMessage]:
        """Private assembly callback; plain-string drain/finish APIs stay unchanged."""
        with self._lock:
            control = self._control(session_id)
        prepared = (observe(control, control.prepare_messages, messages, fallback=[HumanMessage(content=text) for text in messages]) if control is not None
                else [HumanMessage(content=text) for text in messages])
        return self._mark_human_messages(prepared)

    @staticmethod
    def _mark_human_messages(messages):
        result = []
        for message in convert_to_messages(messages):
            extra = {key: value for key, value in message.additional_kwargs.items() if key != 'naiyue_human_input'}
            if isinstance(message, HumanMessage) and not extra.get('naiyue_compaction_summary'):
                extra['naiyue_human_input'] = True
            result.append(message.model_copy(update={'additional_kwargs': extra}))
        return result

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
