"""Application-owned subscriptions and run outcome observation."""

import inspect
import logging
from contextlib import contextmanager
from threading import Lock

from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.agent_runtime.events import (
    RunEventPublisher, RunFailureData, RunFinishedData, RunStartedData, safe_error_type,
    bind_run_event_publisher,
)
from naiyue_test_agent.agent_runtime.tools.bash import BashOutcomeUnknown
from naiyue_test_agent.agent_runtime.tools.write import WriteOutcomeUnknown
from naiyue_test_agent.model_access.errors import classify_model_failure
from naiyue_test_agent.agent_runtime.turn_observation import TurnObserver, bind_turn_observer
from naiyue_test_agent.agent_runtime.control_observation import ControlObserver, bind_control_observer
from naiyue_test_agent.agent_runtime.context_operation_observation import (
    ContextOperationObserver, bind_context_operation_observer,
)


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

    def __init__(self, publisher: RunEventPublisher, mode: str):
        self.publisher = publisher
        self.mode = mode
        self.stage = 'prepare'
        self.status = 'completed'
        self.failure = None
        self._suppress_terminal = False
        self.turns = TurnObserver(publisher)
        self.controls = ControlObserver(publisher)
        self.context_operations = ContextOperationObserver(publisher)

    @contextmanager
    def bind(self):
        if self.mode == 'approval_resume':
            self.controls.submit_approval()
        with (bind_run_event_publisher(self.publisher), bind_turn_observer(self.turns),
              bind_control_observer(self.controls), bind_context_operation_observer(self.context_operations)):
            yield

    def start(self) -> None:
        self.publisher.publish('run_started', RunStartedData(self.mode))
        self.controls.start()

    def result(self, result, pending) -> None:
        self.status = ('interrupted' if pending is not None
                       else 'stopped' if isinstance(result, dict)
                       and result.get('run_stopped') is True else 'completed')
        self.turns.result(interrupted=pending is not None)
        self.controls.run_result(result)
        self.context_operations.finalize()

    def failed(self, error: BaseException, *, stage: str | None = None) -> None:
        if not isinstance(error, Exception) or isinstance(error, GraphBubbleUp):
            self._suppress_terminal = True
            self.context_operations.finalize(error)
            return
        self.status = 'failed'
        try:
            if isinstance(error, (WriteOutcomeUnknown, BashOutcomeUnknown)):
                category, reason = 'tool_outcome_unknown', 'tool_outcome_unknown'
            else:
                category, reason = classify_model_failure(error)
            self.failure = RunFailureData(stage or self.stage, category, reason,
                                          safe_error_type(error))
        except Exception as diagnostic_error:
            logger.error('run_failure_diagnostics_unavailable error_type=%s',
                         safe_error_type(diagnostic_error))
            self.failure = RunFailureData(stage or self.stage, 'unknown', 'unclassified',
                                          safe_error_type(error))
        try:
            self.turns.fail(self.failure)
        except BaseException as report_error:
            logger.error('turn_failure_publish_failed error_type=%s', safe_error_type(report_error))
        try:
            self.context_operations.finalize(error)
        except BaseException as report_error:
            logger.error('context_failure_publish_failed error_type=%s', safe_error_type(report_error))

    def control_close_reason(self):
        return ('control_exit' if self._suppress_terminal else 'prepare_failed'
                if self.stage == 'prepare' and self.status == 'failed' else 'run_' + self.status)

    def cancelled(self, error):
        self.status = 'failed'
        self.failure = RunFailureData(self.stage, 'cancelled', 'task_cancelled', safe_error_type(error))
        try:
            self.turns.fail(self.failure)
            self.context_operations.finalize()
        except BaseException as report_error:
            logger.error('cancel_failure_publish_failed error_type=%s', safe_error_type(report_error))

    def finish(self, manual_review_required: bool) -> None:
        self.controls.flush()
        self.context_operations.flush()
        if not self._suppress_terminal:
            self.publisher.publish('run_finished', RunFinishedData(
                self.status, 'after_turn' if self.status == 'stopped' else None,
                manual_review_required, self.failure,
            ))
