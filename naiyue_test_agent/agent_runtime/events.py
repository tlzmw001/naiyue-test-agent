"""Immutable run events and synchronous, per-run observer delivery."""

import inspect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from collections import deque
from typing import Literal

from naiyue_test_agent.contracts.failures import safe_error_type, RunFailureData
from naiyue_test_agent.contracts.timing import validate_duration
from naiyue_test_agent.contracts.model_events import (
    ModelInvocationStartedData, ModelInvocationFinishedData, check_call_subject as _check_call_subject,
)
from .turn_events import TurnStartedData, TurnFinishedData, TurnUnavailableData
from naiyue_test_agent.contracts.session_events import (
    RunStartedData, RunFinishedData, ControlData, ApprovalRequestedData, SESSION_PAYLOADS,
)
from .context_events import ContextOperationStartedData, ContextOperationFinishedData
from .stream_events import MessageDeltaData, MessageReasoningDeltaData, ToolProgressData
from .context_usage import ModelContextSnapshotData


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelHttpAttemptStartedData:
    attempt_id: str
    invocation_id: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', v)
               for v in (self.attempt_id, self.invocation_id)):
            raise ValueError('Invalid HTTP attempt identity')


@dataclass(frozen=True)
class ModelHttpAttemptFinishedData(ModelHttpAttemptStartedData):
    status: Literal['response_headers', 'unconfirmed']
    http_status: int | None = None
    response_headers_ms: int | None = None

    def __post_init__(self):
        super().__post_init__()
        validate_duration(self.response_headers_ms)
        if self.status == 'response_headers':
            if type(self.http_status) is not int or not 100 <= self.http_status <= 599:
                raise ValueError('Invalid HTTP response status')
        elif self.status != 'unconfirmed' or self.http_status is not None or self.response_headers_ms is not None:
            raise ValueError('Invalid HTTP attempt outcome')


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
    duration_ms: int | None = None

    def __post_init__(self):
        validate_duration(self.duration_ms)
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


EventData = (RunStartedData | RunFinishedData
             | ToolCallStartedData | ToolCallFinishedData | MessageSnapshotData
             | ContextMessagesChangedData | MessageSnapshotUnavailableData
             | TurnStartedData | TurnFinishedData | TurnUnavailableData | ControlData | ApprovalRequestedData
             | ContextOperationStartedData | ContextOperationFinishedData | MessageDeltaData | MessageReasoningDeltaData | ToolProgressData
             | ModelContextSnapshotData | ModelInvocationStartedData | ModelInvocationFinishedData
             | ModelHttpAttemptStartedData | ModelHttpAttemptFinishedData)

_CORE_PAYLOADS = {
    'model_call_started': ModelInvocationStartedData,
    'model_call_finished': ModelInvocationFinishedData,
    'model_http_attempt_started': ModelHttpAttemptStartedData,
    'model_http_attempt_finished': ModelHttpAttemptFinishedData,
    'model_context_snapshot': ModelContextSnapshotData,
    'tool_progress': ToolProgressData,
    'message_delta': MessageDeltaData,
    'message_reasoning_delta': MessageReasoningDeltaData,
    'tool_call_started': ToolCallStartedData, 'tool_call_finished': ToolCallFinishedData,
    'message_completed': MessageSnapshotData, 'message_revised': MessageSnapshotData,
    'context_messages_changed': ContextMessagesChangedData,
    'message_snapshot_unavailable': MessageSnapshotUnavailableData,
    'turn_started': TurnStartedData, 'turn_resumed': TurnStartedData,
    'turn_finished': TurnFinishedData, 'turn_suspended': TurnFinishedData,
    'turn_observation_unavailable': TurnUnavailableData,
    'compaction_started': ContextOperationStartedData, 'compaction_finished': ContextOperationFinishedData,
    'context_recovery_started': ContextOperationStartedData, 'context_recovery_finished': ContextOperationFinishedData,
}

_PAYLOADS = {**_CORE_PAYLOADS, **SESSION_PAYLOADS}


@dataclass(frozen=True)
class AgentEventSource:
    agent_path: str
    agent_id: str
    agent_run_id: str
    parent_run_id: str
    parent_tool_call_id: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip() for value in (
                self.agent_path, self.agent_id, self.agent_run_id, self.parent_run_id, self.parent_tool_call_id)):
            raise ValueError('Agent event source requires complete identifiers')
        if not self.agent_path.startswith('root/') or self.agent_path.rsplit('/', 1)[-1] != self.agent_id:
            raise ValueError('Invalid agent event path')


@dataclass(frozen=True)
class RuntimeEvent:
    event_type: str
    session_id: str
    run_id: str
    sequence: int
    timestamp: str
    data: EventData
    source: AgentEventSource | None = None


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
        self._pending = deque()
        self._delivering = False
        self._lock = RLock()

    def matches(self, session_id: str, run_id: str) -> bool:
        return self._session_id == session_id and self._run_id == run_id

    @property
    def run_id(self):
        return self._run_id

    def publish(self, event_type: str, data: EventData) -> None:
        self._publish(event_type, data, None)

    def for_agent(self, source):
        if not isinstance(source, AgentEventSource) or source.parent_run_id != self._run_id:
            raise ValueError('Agent source does not belong to this root run')
        return AgentEventPublisher(self, source)

    def _publish(self, event_type, data, source):
        with self._lock:
            if _PAYLOADS.get(event_type) is not type(data):
                logger.error('event_payload_invalid')
                return
            self._sequence += 1
            self._pending.append(RuntimeEvent(
                event_type, self._session_id, self._run_id, self._sequence,
                datetime.now(timezone.utc).isoformat(), data, source,
            ))
            if self._delivering:
                return
            self._delivering = True
            try:
                while self._pending:
                    self._deliver(self._pending.popleft())
            finally:
                self._delivering = False

    def _deliver(self, event):
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
            except Exception as error:
                self._failed_handlers.add(index)
                logger.warning('event_observer_failed error_type=%s', safe_error_type(error),
                               extra={'event_observer': {
                                   'event': 'event_observer_failed',
                                   'session_id': self._session_id, 'run_id': self._run_id,
                                   'error_type': safe_error_type(error),
                               }})


class AgentEventPublisher:
    """A source view; root owns ordering, delivery and failed observers."""

    def __init__(self, root, source):
        self._root, self.source = root, source

    @property
    def run_id(self):
        return self.source.agent_run_id

    def matches(self, session_id, run_id):
        return session_id == self._root._session_id and run_id == self.run_id

    def publish(self, event_type, data):
        self._root._publish(event_type, data, self.source)
