"""Application-owned subscriptions and run outcome observation."""

from naiyue_test_agent.contracts.failures import safe_error_type

from naiyue_test_agent.agent_runtime.observation import observe, emit_event

import inspect
import logging
from contextlib import contextmanager
from threading import Lock

from langgraph.errors import GraphBubbleUp
from naiyue_test_agent.contracts.failures import FailureDescription, read_failure_description, RunFailureData
from naiyue_test_agent.contracts.session_events import RunStartedData, RunFinishedData
from naiyue_test_agent.agent_runtime.run_control import RunControl
from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services

from naiyue_test_agent.agent_runtime.events import (
    RunEventPublisher,
)
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy, run_failure_data
from .failure_policy import ApplicationErrorPolicy
from naiyue_test_agent.agent_runtime.turn_observation import TurnObserver
from naiyue_test_agent.agent_runtime.control_observation import ControlObserver
from naiyue_test_agent.agent_runtime.context_operation_observation import (
    ContextOperationObserver,
)
from naiyue_test_agent.agent_runtime.tool_execution.observation import ToolProgressObserver


logger = logging.getLogger(__name__)


def _log_event(event) -> None:
    fields = {
        'event': event.event_type, 'session_id': event.session_id, 'run_id': event.run_id,
        'sequence': event.sequence, 'timestamp': event.timestamp,
    }
    if isinstance(event.data, RunStartedData):
        fields['mode'] = event.data.mode
    elif isinstance(event.data, RunFinishedData):
        fields.update(status=event.data.status, stop_mode=event.data.stop_mode,
                      manual_review_required=event.data.manual_review_required)
    else:
        for name in ('model_call_id', 'model_id', 'purpose', 'status', 'stop_reason',
                     'tool_call_ids', 'input_tokens', 'output_tokens', 'cache_read_tokens',
                     'tool_call_id', 'tool_name', 'boundary', 'outcome', 'reason',
                     'message_id', 'message_type', 'visibility', 'snapshot_bytes',
                     'is_context_summary', 'removed_count', 'current_count', 'stage',
                     'error_type', 'turn_id', 'origin_run_id', 'kind', 'previous_turn_id',
                     'assistant_message_id', 'error_result_count', 'control_id', 'approved_count',
                     'rejected_count', 'stop_mode', 'count', 'operation_id', 'operation', 'trigger',
                     'before_estimate', 'original_message_count', 'after_estimate', 'removed_message_count',
                     'preserved_message_count', 'summary_input_tokens', 'summary_output_tokens',
                     'summary_duration_ms', 'summary_message_id', 'related_operation_id', 'retry_attempts'):
            if hasattr(event.data, name):
                fields[name] = getattr(event.data, name)
    failure = getattr(event.data, 'failure', None)
    if failure is not None:
        fields.update(stage=failure.stage, category=failure.category,
                      reason=failure.reason, error_type=failure.error_type)
    failed = fields.get('status') == 'failed' or fields.get('outcome') in ('exception', 'outcome_unknown')
    level = (logging.ERROR if failed else logging.WARNING if event.event_type == 'control_outcome_unknown'
             else logging.INFO)
    logger.log(level,
               'application_run_event', extra={'application_event': fields})


class ApplicationEventSubscriptions:
    def __init__(self):
        self._handlers = {}
        self._lock = Lock()

    def subscribe(self, handler):
        if (not callable(handler) or inspect.iscoroutinefunction(handler)
                or inspect.iscoroutinefunction(getattr(handler, '__call__', None))):
            raise TypeError('A synchronous run event observer is required')
        token = object()
        with self._lock:
            self._handlers[token] = handler

        def unsubscribe():
            with self._lock:
                self._handlers.pop(token, None)

        return unsubscribe

    def publisher(self, session_id: str, run_id: str) -> RunEventPublisher:
        with self._lock:
            handlers = (_log_event, *self._handlers.values())
        return RunEventPublisher(session_id, run_id, handlers)


class RunObservation:
    """Observe outcomes; the session service still owns results and cleanup."""

    def __init__(self, publisher: RunEventPublisher, mode: str, *, progress_settings=None):
        self.publisher = publisher
        self.mode = mode
        self.stage = 'prepare'
        self.status = 'completed'
        self.failure = None
        self._suppress_terminal = False
        self.turns = TurnObserver(publisher)
        self.controls = ControlObserver(publisher)
        self.stop_control = RunControl()
        self.context_operations = ContextOperationObserver(publisher)
        self.tool_progress = ToolProgressObserver(publisher, progress_settings)
        self.runtime_errors = RuntimeErrorPolicy()
        self.application_errors = ApplicationErrorPolicy()

    def _describe_failure(self, error):
        reported = read_failure_description(error, run_id=self.publisher.run_id)
        if reported is not None:
            return reported
        description = self.application_errors.try_describe(error)
        if description is not None:
            return description
        return (self.runtime_errors.describe(error) if self.stage == 'execute' else
                FailureDescription('application', 'unknown', 'unclassified', safe_error_type(error)))

    @contextmanager
    def bind(self):
        if self.mode == 'approval_resume':
            observe(self.controls, self.controls.submit_approval)
        services = RunServices(self.publisher, self.runtime_errors, self.stop_control,
                               self.turns, self.controls, self.context_operations, tool_progress=self.tool_progress)
        with bind_run_services(services):
            yield

    def start(self) -> None:
        emit_event(self.publisher, 'run_started', lambda: RunStartedData(self.mode))
        observe(self.controls, self.controls.start)

    def result(self, result, pending) -> None:
        self.status = ('interrupted' if pending is not None
                       else 'stopped' if isinstance(result, dict)
                       and result.get('run_stopped') is True else 'completed')
        observe(self.turns, self.turns.result, interrupted=pending is not None)
        observe(self.controls, self.controls.run_result, result)
        observe(self.context_operations, self.context_operations.finalize)

    def failed(self, error: BaseException, *, stage: str | None = None) -> None:
        if not isinstance(error, Exception) or isinstance(error, GraphBubbleUp):
            self._suppress_terminal = True
            observe(self.context_operations, self.context_operations.finalize, error, diagnostic='context_failure_publish_failed')
            return
        self.status = 'failed'
        try:
            self.failure = run_failure_data(self._describe_failure(error), stage=stage or self.stage)
        except Exception as diagnostic_error:
            logger.error('run_failure_diagnostics_unavailable error_type=%s',
                         safe_error_type(diagnostic_error))
            self.failure = RunFailureData(stage or self.stage, 'unknown', 'unclassified',
                                          safe_error_type(error))
        observe(self.turns, self.turns.fail, self.failure, diagnostic='turn_failure_publish_failed')
        observe(self.context_operations, self.context_operations.finalize, error, diagnostic='context_failure_publish_failed')

    def control_close_reason(self):
        return ('control_exit' if self._suppress_terminal else 'prepare_failed'
                if self.stage == 'prepare' and self.status == 'failed' else 'run_' + self.status)

    def cancelled(self, error):
        self.status = 'failed'
        self.failure = run_failure_data(self.runtime_errors.describe(error), stage=self.stage)
        observe(self.turns, self.turns.fail, self.failure)
        observe(self.context_operations, self.context_operations.finalize)

    def finish(self, manual_review_required: bool) -> None:
        observe(self.controls, self.controls.flush)
        observe(self.context_operations, self.context_operations.flush)
        if not self._suppress_terminal:
            emit_event(self.publisher, 'run_finished', lambda: RunFinishedData(
                self.status, 'active' if self.status == 'stopped' else None,
                manual_review_required, self.failure,
            ))
