"""Strict event-sequence checks belong to tests, outside isolated listeners."""

from naiyue_test_agent.agent_runtime.events import _PAYLOADS, RunEventPublisher


class EventSequenceChecker:
    def __init__(self):
        self._sequence = 0
        self._finished = False
        self._model_invocations = set()
        self._http_attempts = {}
        self._tool_calls = {}
        self._turns = {}
        self._controls = {}
        self._context_operations = {}

    def check(self, event_type, data):
        if (self._finished or _PAYLOADS.get(event_type) is not type(data)
                or (event_type == 'run_started') != (self._sequence == 0)):
            raise ValueError('Invalid run event lifecycle')
        if event_type in ('compaction_started', 'compaction_finished', 'context_recovery_started', 'context_recovery_finished'):
            operation = 'compaction' if event_type.startswith('compaction_') else 'recovery'
            if data.operation != operation:
                raise ValueError('Invalid context operation kind')
            subject = (operation, data.model_id, data.model_call_id, data.trigger,
                       data.before_estimate, data.original_message_count)
            if event_type.endswith('_started'):
                if data.operation_id in self._context_operations:
                    raise ValueError('Context operation already started')
                self._context_operations[data.operation_id] = subject
            else:
                if self._context_operations.get(data.operation_id) != subject:
                    raise ValueError('Context result does not match its start')
                del self._context_operations[data.operation_id]
        if event_type.startswith('control_'):
            key = data.control_id
            subject = (data.kind, data.tool_call_ids, data.decisions, data.origin_run_id)
            recorded = self._controls.get(key)
            if recorded is not None and recorded[0] != subject:
                raise ValueError('Control subject changed')
            previous = (data.kind, recorded[1]) if recorded is not None else None
            if event_type == 'control_accepted':
                valid = previous is None
            elif event_type == 'control_delivery_started':
                valid = previous == (data.kind, 'accepted') and data.kind != 'stop'
            elif event_type == 'control_delivered':
                valid = (previous == (data.kind, 'selected')
                         and data.kind in ('steering', 'follow_up') and data.message_id is not None
                         and data.visibility is not None and data.boundary == 'message_state')
            elif event_type == 'control_applied':
                valid = ((previous == ('stop', 'accepted') and data.boundary == 'active')
                         or (previous == ('approval', 'selected') and data.boundary == 'approval_hook'))
            elif event_type == 'control_outcome_unknown':
                valid = previous == (data.kind, 'selected') and data.reason is not None
            else:
                valid = previous == (data.kind, 'accepted') and data.reason is not None
            if not valid:
                raise ValueError('Invalid control lifecycle')
            if event_type in ('control_accepted', 'control_delivery_started'):
                self._controls[key] = (subject, 'accepted' if event_type == 'control_accepted' else 'selected')
            else:
                del self._controls[key]
        if event_type in ('turn_started', 'turn_resumed', 'turn_finished', 'turn_suspended'):
            key = data.turn_id
            subject = (data.origin_run_id, data.model_call_id)
            if event_type in ('turn_started', 'turn_resumed'):
                if (key in self._turns or self._turns
                        or (event_type == 'turn_resumed') != (data.kind == 'approval_resume')):
                    raise ValueError('Invalid turn start lifecycle')
                self._turns[key] = subject
            else:
                if (self._turns.get(key) != subject
                        or (event_type == 'turn_suspended') != (data.status == 'suspended')):
                    raise ValueError('Turn result does not match its start')
                del self._turns[key]
        if event_type.startswith('tool_call_'):
            key, subject, calls = data.tool_call_id, (data.tool_name, data.boundary), self._tool_calls
        else:
            calls = None
        if calls is not None:
            if not isinstance(key, str) or not key:
                raise ValueError('A call identifier is required')
            if event_type.endswith('_started'):
                if key in calls:
                    raise ValueError('Call already started')
                calls[key] = subject
            else:
                if key not in calls or calls[key] != subject:
                    raise ValueError('Call result does not match its start')
                del calls[key]
        if event_type.startswith('model_http_attempt_'):
            key = data.attempt_id
            if event_type.endswith('_started'):
                if key in self._http_attempts or data.invocation_id not in self._model_invocations:
                    raise ValueError('Invalid HTTP attempt start')
                self._http_attempts[key] = data.invocation_id
            else:
                if self._http_attempts.get(key) != data.invocation_id:
                    raise ValueError('HTTP attempt result does not match its start')
                del self._http_attempts[key]
        if event_type.startswith('model_call_'):
            key = data.invocation_id
            if event_type.endswith('_started'):
                if key in self._model_invocations:
                    raise ValueError('Model invocation already started')
                self._model_invocations.add(key)
            else:
                if key not in self._model_invocations:
                    raise ValueError('Model invocation result has no start')
                if key in self._http_attempts.values():
                    raise ValueError('Model terminal has unfinished HTTP attempts')
                self._model_invocations.remove(key)
        self._sequence += 1
        self._finished = event_type == 'run_finished'

    @classmethod
    def assert_valid(cls, events, *, require_terminal=True):
        checker = cls()
        for event in events:
            if event.sequence != checker._sequence + 1:
                raise AssertionError('Event sequence is not contiguous')
            checker.check(event.event_type, event.data)
        if require_terminal and not checker._finished:
            raise AssertionError('Run terminal event is missing')

    @classmethod
    def assert_healthy(cls, events, records):
        """Normal-path tests must reject isolated faults, outside delivery."""
        faults = []
        for record in records:
            if not record.name.startswith('naiyue_test_agent.'):
                continue
            code = record.getMessage().split()[0]
            if code in ('observation_failed', 'event_observer_failed', 'event_payload_invalid',
                        'control_message_batch_unavailable', 'model_http_context_unavailable',
                        'model_http_capture_incomplete') or code.endswith(
                            ('_publish_failed', '_observation_failed', '_reporting_failed')):
                faults.append(record.getMessage())
        assert not faults, f'Unexpected observation diagnostics: {faults}'
        unavailable = [event.event_type for event in events if event.event_type in
                       ('message_snapshot_unavailable', 'turn_observation_unavailable')]
        assert not unavailable, f'Unexpected observation unavailability: {unavailable}'
        cls.assert_valid(events)


class CheckedPublisher:
    """Validate explicitly BEFORE ordinary delivery; faults cannot be swallowed."""

    def __init__(self, *args):
        self.publisher = RunEventPublisher(*args)
        self.checker = EventSequenceChecker()

    def publish(self, event_type, data):
        self.checker.check(event_type, data)
        self.publisher.publish(event_type, data)
