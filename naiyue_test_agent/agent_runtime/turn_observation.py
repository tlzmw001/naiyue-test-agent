"""Logical turn observation and JSON-only approval continuation."""

from threading import RLock
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from .observation import observe
from .turn_events import TurnFinishedData, TurnStartedData, TurnUnavailableData


class TurnObserver:
    def __init__(self, publisher):
        self.publisher = publisher
        self._state = None
        self._phase = 'idle'
        self._reply = None
        self._ready = False
        self._visible = False
        self._recovery_only = False
        self._disabled = False
        self._lock = RLock()

    def _new_state(self, model_call_id):
        return {'turn_id': uuid4().hex, 'origin_run_id': self.publisher.run_id,
                'model_call_id': model_call_id, 'assistant_message_id': None,
                'tool_call_ids': (), 'tool_message_ids': (), 'error_result_count': 0}

    def begin(self, model_call_id):
        with self._lock:
            if self._disabled:
                return
            self.try_finish()
            if self._phase == 'active':
                self.disable('incomplete_turn')
                return
            self._state = self._new_state(model_call_id)
            self._phase = 'active'
            self._ready = self._visible = self._recovery_only = False
            self.publisher.publish('turn_started', TurnStartedData(
                self._state['turn_id'], self._state['origin_run_id'], model_call_id,
            ))

    def accept(self, message):
        with self._lock:
            if self._phase != 'active' or self._disabled:
                return
            ids = tuple(c['id'] for c in message.tool_calls if isinstance(c.get('id'), str) and c['id'])
            if len(ids) != len(message.tool_calls):
                self.disable('unbound_tool_call')
                return
            self._reply = message
            self._state['tool_call_ids'] = ids

    def mark_ready(self):
        with self._lock:
            if self._phase == 'active' and not self._disabled:
                self._ready = True

    def observe(self, messages, *, visibility='graph_values'):
        with self._lock:
            if self._disabled or self._phase != 'active':
                return
            message_id = self._state['assistant_message_id']
            if self._reply is not None:
                message_id = self._reply.id
            if not isinstance(message_id, str) or not message_id:
                return
            index = next((i for i, m in enumerate(messages)
                          if isinstance(m, AIMessage) and m.id == message_id), None)
            if index is None:
                return
            self._state['assistant_message_id'] = message_id
            self._reply = None
            self._visible = True
            self._recovery_only = visibility == 'recovery_view'
            results = {}
            for message in messages[index + 1:]:
                if isinstance(message, AIMessage):
                    break
                if (isinstance(message, ToolMessage) and message.tool_call_id in self._state['tool_call_ids']
                        and isinstance(message.id, str) and message.id):
                    if message.tool_call_id in results:
                        self.disable('ambiguous_tool_results')
                        return
                    results[message.tool_call_id] = message
            ordered = [results[key] for key in self._state['tool_call_ids'] if key in results]
            self._state['tool_message_ids'] = tuple(m.id for m in ordered)
            self._state['error_result_count'] = sum(m.status == 'error' for m in ordered)
            if visibility == 'graph_values':
                self.try_finish()

    def try_finish(self):
        with self._lock:
            if (not self._disabled and self._phase == 'active' and self._ready
                    and self._visible and not self._recovery_only
                    and len(self._state['tool_call_ids']) == len(self._state['tool_message_ids'])):
                self._finish('completed')

    def _finish(self, status, failure=None):
        data = TurnFinishedData(**self._state, status=status, failure=failure)
        self._phase = status
        self._reply = None
        self.publisher.publish('turn_suspended' if status == 'suspended' else 'turn_finished', data)
        if status == 'completed':
            self._state = None

    def result(self, *, interrupted):
        with self._lock:
            if self._disabled or self._phase != 'active':
                return
            if interrupted:
                self._finish('suspended')
            else:
                self.try_finish()
                if self._phase == 'active':
                    self.disable('incomplete_turn')

    def fail(self, failure):
        with self._lock:
            if not self._disabled and self._phase == 'active':
                self._finish('failed', failure)

    def disable(self, reason='state_observation_unavailable'):
        with self._lock:
            if not self._disabled:
                self._disabled = True
                self._reply = None
                self.publisher.publish('turn_observation_unavailable', TurnUnavailableData(
                    self._state['turn_id'] if self._state else None, reason,
                ))

    def snapshot(self, *, pending):
        with self._lock:
            if not pending or self._state is None or self._phase not in ('suspended', 'failed'):
                return None
            return {**self._state, 'version': 1, 'status': self._phase,
                    'post_model_checks_passed': self._ready,
                    'tool_call_ids': list(self._state['tool_call_ids']),
                    'tool_message_ids': list(self._state['tool_message_ids'])}

    def restore(self, saved):
        if saved is None:
            return
        keys = {'version', 'status', 'turn_id', 'origin_run_id', 'model_call_id',
                'assistant_message_id', 'tool_call_ids', 'tool_message_ids', 'error_result_count',
                'post_model_checks_passed'}
        if (type(saved) is not dict or set(saved) != keys or type(saved['version']) is not int
                or saved['version'] != 1 or saved['status'] not in ('suspended', 'failed')
                or type(saved['post_model_checks_passed']) is not bool
                or type(saved['tool_call_ids']) is not list or type(saved['tool_message_ids']) is not list):
            raise ValueError('Invalid turn continuation')
        state = {k: v for k, v in saved.items()
                 if k not in ('version', 'status', 'post_model_checks_passed')}
        state['tool_call_ids'] = tuple(state['tool_call_ids'])
        state['tool_message_ids'] = tuple(state['tool_message_ids'])
        # The suspended schema validates identity/results without inventing failure details.
        TurnFinishedData(**state, status='suspended')
        with self._lock:
            self._state = state
            self._phase = saved['status']
            self._ready = saved['post_model_checks_passed']

    def activate_resume(self, baseline):
        with self._lock:
            if self._disabled:
                return
            if self._phase == 'suspended':
                self.publisher.publish('turn_resumed', TurnStartedData(
                    self._state['turn_id'], self._state['origin_run_id'],
                    self._state['model_call_id'], kind='approval_resume',
                ))
            else:
                previous_id = self._state['turn_id'] if self._state else None
                previous = self._state
                self._state = self._new_state(None)
                if previous and previous['assistant_message_id']:
                    self._state.update(assistant_message_id=previous['assistant_message_id'],
                                       tool_call_ids=previous['tool_call_ids'])
                else:
                    reply = next((m for m in reversed(baseline) if isinstance(m, AIMessage)), None)
                    if reply is None or not reply.id or not reply.tool_calls:
                        self.disable('missing_approval_context')
                        return
                    self._state.update(assistant_message_id=reply.id,
                                       tool_call_ids=tuple(c['id'] for c in reply.tool_calls))
                self.publisher.publish('turn_started', TurnStartedData(
                    self._state['turn_id'], self._state['origin_run_id'], None,
                    kind='approval_retry', previous_turn_id=previous_id,
                ))
            self._phase = 'active'
            self._visible = self._recovery_only = False


class TurnBoundaryMiddleware(AgentMiddleware):
    """Last after_model hook; observation only, no state update or routing."""

    def after_model(self, state, runtime):
        observer = runtime.context.get('_turn_observer') if runtime.context else None
        if observer is not None:
            observe(observer, observer.mark_ready)
        return None
