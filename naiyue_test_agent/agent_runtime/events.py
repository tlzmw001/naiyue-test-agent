"""Immutable run events and synchronous, per-run observer delivery."""

import inspect
import logging
import re
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Literal

from langgraph.errors import GraphBubbleUp
from .turn_events import TurnStartedData, TurnFinishedData, TurnUnavailableData
from .control_events import ControlData, ApprovalRequestedData
from .context_events import ContextOperationStartedData, ContextOperationFinishedData
from .stream_events import MessageDeltaData


logger = logging.getLogger(__name__)


def safe_error_type(error: BaseException) -> str:
    name = type(error).__name__
    return name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', name) else 'Exception'


@dataclass(frozen=True)
class RunFailureData:
    stage: Literal['prepare', 'execute', 'finalize', 'cleanup']
    category: str
    reason: str
    error_type: str

    def __post_init__(self):
        if self.stage not in ('prepare', 'execute', 'finalize', 'cleanup'):
            raise ValueError('Invalid run failure stage')
        if any(not isinstance(value, str)
               or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', value)
               for value in (self.category, self.reason)):
            raise ValueError('Run failure requires fixed diagnostic codes')
        if (not isinstance(self.error_type, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', self.error_type)):
            raise ValueError('Invalid run failure error type')


@dataclass(frozen=True)
class RunStartedData:
    mode: Literal['message', 'approval_resume']

    def __post_init__(self):
        if self.mode not in ('message', 'approval_resume'):
            raise ValueError('Invalid run start mode')


@dataclass(frozen=True)
class RunFinishedData:
    status: Literal['completed', 'stopped', 'interrupted', 'failed']
    stop_mode: Literal['active'] | None = None
    manual_review_required: bool = False
    failure: RunFailureData | None = None

    def __post_init__(self):
        if self.status not in ('completed', 'stopped', 'interrupted', 'failed'):
            raise ValueError('Invalid run terminal status')
        if self.stop_mode != ('active' if self.status == 'stopped' else None):
            raise ValueError('Invalid run stop mode')
        if type(self.manual_review_required) is not bool:
            raise ValueError('Invalid manual review flag')
        if (self.status == 'failed') != isinstance(self.failure, RunFailureData):
            raise ValueError('Invalid run failure details')


def _check_call_subject(call_id, label):
    if not isinstance(call_id, str) or not call_id:
        raise ValueError('A call identifier is required')
    if label is not None and (not isinstance(label, str)
                              or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', label)):
        raise ValueError('Invalid call label')


@dataclass(frozen=True)
class ModelCallStartedData:
    model_call_id: str
    model_id: str | None
    purpose: Literal['agent'] = 'agent'

    def __post_init__(self):
        _check_call_subject(self.model_call_id, self.model_id)
        if self.purpose != 'agent':
            raise ValueError('Invalid model call purpose')


@dataclass(frozen=True)
class ModelCallFinishedData:
    model_call_id: str
    model_id: str | None
    status: Literal['accepted', 'failed']
    stop_reason: str | None = None
    tool_call_ids: tuple[str, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    failure: RunFailureData | None = None

    def __post_init__(self):
        _check_call_subject(self.model_call_id, self.model_id)
        if self.status not in ('accepted', 'failed'):
            raise ValueError('Invalid model call outcome')
        if self.stop_reason not in (None, 'stop', 'tool_use', 'length', 'error', 'unknown'):
            raise ValueError('Invalid normalized stop reason')
        if (self.status == 'failed') != isinstance(self.failure, RunFailureData):
            raise ValueError('Invalid model failure details')
        if (type(self.tool_call_ids) is not tuple
                or any(not isinstance(item, str) for item in self.tool_call_ids)):
            raise ValueError('Tool identifiers must be an immutable tuple')
        if any(value is not None and (type(value) is not int or value < 0)
               for value in (self.input_tokens, self.output_tokens, self.cache_read_tokens)):
            raise ValueError('Invalid model usage metadata')


@dataclass(frozen=True)
class ToolCallStartedData:
    tool_call_id: str
    tool_name: str | None
    boundary: Literal['dispatch', 'approval_resolution'] = 'dispatch'

    def __post_init__(self):
        _check_call_subject(self.tool_call_id, self.tool_name)
        if self.boundary not in ('dispatch', 'approval_resolution'):
            raise ValueError('Invalid tool observation boundary')


@dataclass(frozen=True)
class ToolCallFinishedData:
    tool_call_id: str
    tool_name: str | None
    boundary: Literal['dispatch', 'approval_resolution']
    outcome: Literal['success', 'error_result', 'not_executed', 'exception', 'outcome_unknown']
    reason: str | None = None
    failure: RunFailureData | None = None

    def __post_init__(self):
        _check_call_subject(self.tool_call_id, self.tool_name)
        if self.boundary not in ('dispatch', 'approval_resolution'):
            raise ValueError('Invalid tool observation boundary')
        if self.outcome not in ('success', 'error_result', 'not_executed',
                                'exception', 'outcome_unknown'):
            raise ValueError('Invalid tool outcome')
        if (self.outcome in ('exception', 'outcome_unknown')) != isinstance(self.failure, RunFailureData):
            raise ValueError('Invalid tool failure details')
        if self.reason is not None and (not isinstance(self.reason, str)
                                        or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', self.reason)):
            raise ValueError('Invalid tool reason code')


@dataclass(frozen=True)
class MessageSnapshotData:
    message_id: str
    message_type: str
    visibility: Literal['graph_values', 'recovery_view']
    snapshot_json: str
    snapshot_sha256: str
    snapshot_bytes: int
    is_context_summary: bool = False
    tool_call_id: str | None = None
    status: Literal['success', 'error'] | None = None

    def __post_init__(self):
        if not isinstance(self.message_id, str) or not self.message_id:
            raise ValueError('A real message identifier is required')
        if self.message_type not in ('human', 'ai', 'tool', 'system', 'function', 'chat'):
            raise ValueError('Unsupported message type')
        if self.visibility not in ('graph_values', 'recovery_view'):
            raise ValueError('Invalid message visibility')
        if (not isinstance(self.snapshot_json, str)
                or type(self.snapshot_bytes) is not int or self.snapshot_bytes < 0
                or self.snapshot_bytes != len(self.snapshot_json.encode('utf-8'))
                or not isinstance(self.snapshot_sha256, str)
                or not re.fullmatch(r'[a-f0-9]{64}', self.snapshot_sha256)):
            raise ValueError('Invalid immutable message snapshot')
        if type(self.is_context_summary) is not bool:
            raise ValueError('Invalid context summary flag')
        if self.tool_call_id is not None and not isinstance(self.tool_call_id, str):
            raise ValueError('Invalid tool call identifier')
        if self.status not in (None, 'success', 'error'):
            raise ValueError('Invalid tool message status')


@dataclass(frozen=True)
class ContextMessagesChangedData:
    removed_message_ids: tuple[str, ...]
    current_message_ids: tuple[str, ...]
    visibility: Literal['graph_values', 'recovery_view']
    reason: Literal['compaction', 'state_update']

    def __post_init__(self):
        for values in (self.removed_message_ids, self.current_message_ids):
            if type(values) is not tuple or any(not isinstance(v, str) or not v for v in values):
                raise ValueError('Immutable message identifiers are required')
        if self.visibility not in ('graph_values', 'recovery_view'):
            raise ValueError('Invalid context visibility')
        if self.reason not in ('compaction', 'state_update'):
            raise ValueError('Invalid context change reason')

    @property
    def removed_count(self):
        return len(self.removed_message_ids)

    @property
    def current_count(self):
        return len(self.current_message_ids)


@dataclass(frozen=True)
class MessageSnapshotUnavailableData:
    stage: Literal['baseline', 'stream', 'snapshot', 'recovery']
    reason: str
    message_id: str | None = None
    message_type: str | None = None
    visibility: Literal['graph_values', 'recovery_view'] | None = None
    error_type: str | None = None

    def __post_init__(self):
        if self.stage not in ('baseline', 'stream', 'snapshot', 'recovery'):
            raise ValueError('Invalid observation stage')
        if self.reason not in ('unsupported_state_interface', 'baseline_read_failed',
                               'recovery_read_failed', 'unsupported_projection',
                               'missing_message_id', 'unsupported_message_type',
                               'invalid_message_collection', 'duplicate_message_id'):
            raise ValueError('Invalid observation reason')
        if self.visibility not in (None, 'graph_values', 'recovery_view'):
            raise ValueError('Invalid observation visibility')
        if any(v is not None and not isinstance(v, str)
               for v in (self.message_id, self.message_type, self.error_type)):
            raise ValueError('Invalid observation metadata')
        if self.error_type is not None and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', self.error_type):
            raise ValueError('Invalid observation error type')


EventData = (RunStartedData | RunFinishedData | ModelCallStartedData | ModelCallFinishedData
             | ToolCallStartedData | ToolCallFinishedData | MessageSnapshotData
             | ContextMessagesChangedData | MessageSnapshotUnavailableData
             | TurnStartedData | TurnFinishedData | TurnUnavailableData | ControlData | ApprovalRequestedData
             | ContextOperationStartedData | ContextOperationFinishedData | MessageDeltaData)

_PAYLOADS = {
    'message_delta': MessageDeltaData,
    'run_started': RunStartedData, 'run_finished': RunFinishedData,
    'model_call_started': ModelCallStartedData, 'model_call_finished': ModelCallFinishedData,
    'tool_call_started': ToolCallStartedData, 'tool_call_finished': ToolCallFinishedData,
    'message_completed': MessageSnapshotData, 'message_revised': MessageSnapshotData,
    'context_messages_changed': ContextMessagesChangedData,
    'message_snapshot_unavailable': MessageSnapshotUnavailableData,
    'turn_started': TurnStartedData, 'turn_resumed': TurnStartedData,
    'turn_finished': TurnFinishedData, 'turn_suspended': TurnFinishedData,
    'turn_observation_unavailable': TurnUnavailableData,
    'control_accepted': ControlData, 'control_delivery_started': ControlData,
    'control_delivered': ControlData, 'control_applied': ControlData,
    'control_discarded': ControlData, 'control_outcome_unknown': ControlData,
    'approval_requested': ApprovalRequestedData,
    'compaction_started': ContextOperationStartedData, 'compaction_finished': ContextOperationFinishedData,
    'context_recovery_started': ContextOperationStartedData, 'context_recovery_finished': ContextOperationFinishedData,
}


@dataclass(frozen=True)
class RuntimeEvent:
    event_type: str
    session_id: str
    run_id: str
    sequence: int
    timestamp: str
    data: EventData


class RunEventPublisher:
    """Serialize lightweight notifications without holding a session lock."""

    def __init__(self, session_id: str, run_id: str,
                 handlers: tuple[Callable[[RuntimeEvent], None], ...]):
        if any(not isinstance(value, str) or not value.strip()
               for value in (session_id, run_id)):
            raise ValueError('Non-empty run correlation identifiers are required')
        self._session_id = session_id
        self._run_id = run_id
        self._handlers = tuple(handlers)
        self._failed_handlers: set[int] = set()
        self._sequence = 0
        self._finished = False
        self._model_calls = {}
        self._tool_calls = {}
        self._turns = {}
        self._controls = {}
        self._context_operations = {}
        self._lock = Lock()

    def matches(self, session_id: str, run_id: str) -> bool:
        return self._session_id == session_id and self._run_id == run_id

    @property
    def run_id(self):
        return self._run_id

    def publish(self, event_type: str, data: EventData) -> None:
        with self._lock:
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
            if event_type.startswith('model_call_'):
                key, subject, calls = data.model_call_id, data.model_id, self._model_calls
            elif event_type.startswith('tool_call_'):
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
            self._sequence += 1
            self._finished = event_type == 'run_finished'
            event = RuntimeEvent(
                event_type, self._session_id, self._run_id, self._sequence,
                datetime.now(timezone.utc).isoformat(), data,
            )
            for index, handler in enumerate(self._handlers):
                if index in self._failed_handlers:
                    continue
                try:
                    returned = handler(event)
                    if inspect.isawaitable(returned):
                        if inspect.iscoroutine(returned):
                            returned.close()
                        raise TypeError('Run event observers must be synchronous')
                    if returned is not None:
                        raise TypeError('Run event observers must return None')
                except GraphBubbleUp:
                    raise
                except Exception as error:
                    self._failed_handlers.add(index)
                    logger.warning('event_observer_failed error_type=%s', safe_error_type(error),
                                   extra={'event_observer': {
                                       'event': 'event_observer_failed',
                                       'session_id': self._session_id, 'run_id': self._run_id,
                                       'error_type': safe_error_type(error),
                                   }})


_BOUND_PUBLISHER: ContextVar[RunEventPublisher | None] = ContextVar(
    'naiyue_run_event_publisher', default=None,
)


@contextmanager
def bind_run_event_publisher(publisher: RunEventPublisher):
    """Bridge existing invocation signatures into explicit Runtime.context."""
    token = _BOUND_PUBLISHER.set(publisher)
    try:
        yield
    finally:
        _BOUND_PUBLISHER.reset(token)


def current_run_event_publisher() -> RunEventPublisher | None:
    return _BOUND_PUBLISHER.get()
