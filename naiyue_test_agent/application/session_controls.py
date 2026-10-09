"""Session queue controls; the session service owns storage, lock, and errors."""

import logging

from langchain_core.messages import HumanMessage


logger = logging.getLogger('naiyue_test_agent.application')


class SessionControlMixin:
    """Internal split for the 500-line limit; public control signatures remain."""

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
                control.accept('steering', queue_size=size)
        self._record_steering('steering_accepted', session_id, run_id, 1)
        if control:
            control.flush()

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
                control.accept('follow_up', queue_size=size)
        self._record_follow_up('follow_up_accepted', session_id, run_id, 1)
        if control:
            control.flush()

    def request_stop_after_turn(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions[session_id]
            if not session['busy']:
                raise self._busy_error('Session has no active run to stop')
            if session['stop_requested']:
                return
            if not session['steering_open']:
                raise self._busy_error('Session is no longer accepting stop requests')
            session['stop_requested'] = True
            session['steering_open'] = False
            run_id = session['run_id']
            control = self._control(session_id)
            if control:
                control.accept('stop')
        logger.info('stop_requested', extra={'stop_event': {
            'event': 'stop_requested', 'session_id': session_id, 'run_id': run_id,
        }})
        if control:
            control.flush()

    def is_stop_requested(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions[session_id]['stop_requested']

    def _select_controls(self, session_id, kind, messages):
        control = self._control(session_id)
        if control and messages:
            control.select(kind, len(messages))
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
            control.flush()
        return messages

    def finish_steering(self, session_id: str) -> list[str]:
        with self._lock:
            session = self._sessions[session_id]
            messages = list(session['steering_queue'])
            session['steering_queue'].clear()
            if not messages:
                session['steering_open'] = False
            run_id = session['run_id']
            control = self._select_controls(session_id, 'steering', messages)
        if messages:
            self._record_steering('steering_injected', session_id, run_id, len(messages))
        if control:
            control.flush()
        return messages

    def finish_pending_messages(self, session_id: str) -> list[str]:
        """Preserve the message-only callback for older interactive assembly."""
        _stopped, messages = self.finish_run_boundary(session_id)
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
            control.flush()
        return False, messages

    def prepare_control_messages(self, session_id: str, messages: list[str]) -> list[HumanMessage]:
        """Private assembly callback; plain-string drain/finish APIs stay unchanged."""
        with self._lock:
            control = self._control(session_id)
        return (control.prepare_messages(messages) if control is not None
                else [HumanMessage(content=text) for text in messages])

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
