"""Application-owned subscriptions and run outcome observation."""

from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.contracts.timing import start_timer, elapsed_ms
from naiyue_test_agent.model_access.http_observation import bind_http_run

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
    RunEventPublisher, AgentEventPublisher,
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
    if event.source is not None:
        fields.update(agent_path=event.source.agent_path, agent_run_id=event.source.agent_run_id,
                      parent_tool_call_id=event.source.parent_tool_call_id)
    if isinstance(event.data, RunStartedData):
        fields['mode'] = event.data.mode
        fields['http_observation_version'] = event.data.http_observation_version
    elif isinstance(event.data, RunFinishedData):
        fields.update(status=event.data.status, stop_mode=event.data.stop_mode,
                      manual_review_required=event.data.manual_review_required, duration_ms=event.data.duration_ms,
                      http_capture_status=event.data.http_capture_status)
    else:
        for name in ('invocation_id', 'usage_status', 'model_call_id', 'model_id', 'purpose', 'status', 'stop_reason',
                     'tool_call_ids', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
                     'tool_call_id', 'tool_name', 'boundary', 'outcome', 'reason',
                     'message_id', 'message_type', 'visibility', 'snapshot_bytes',
                     'is_context_summary', 'removed_count', 'current_count', 'stage',
                     'error_type', 'turn_id', 'origin_run_id', 'kind', 'previous_turn_id',
                     'assistant_message_id', 'error_result_count', 'control_id', 'approved_count',
                     'rejected_count', 'stop_mode', 'count', 'operation_id', 'operation', 'trigger',
                     'before_estimate', 'original_message_count', 'after_estimate', 'removed_message_count',
                     'preserved_message_count', 'summary_input_tokens', 'summary_output_tokens',
                     'summary_duration_ms', 'summary_message_id', 'related_operation_id', 'retry_attempts',
                     'summary_cache_read_tokens', 'summary_cache_write_tokens', 'duration_ms',
                     'attempt_id', 'http_status', 'response_headers_ms', 'http_capture_status'):
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

    def subscribe(self, handler, *, session_id=None):
        if session_id is not None and (not isinstance(session_id, str) or not session_id.strip()):
            raise ValueError('A non-empty session subscription scope is required')
        if (not callable(handler) or inspect.iscoroutinefunction(handler)
                or inspect.iscoroutinefunction(getattr(handler, '__call__', None))):
            raise TypeError('A synchronous run event observer is required')
        token = object()
        with self._lock:
            self._handlers[token] = (session_id, handler)

        def unsubscribe():
            with self._lock:
                self._handlers.pop(token, None)

        return unsubscribe

    def publisher(self, session_id: str, run_id: str) -> RunEventPublisher:
        with self._lock:
            handlers = (_log_event, *(handler for scope, handler in self._handlers.values()
                                      if scope is None or scope == session_id))
        return RunEventPublisher(session_id, run_id, handlers)


class RunObservation:
    """Observe outcomes; the calling execution owner handles results and cleanup."""

    def __init__(self, publisher: RunEventPublisher | AgentEventPublisher, mode: str, *, progress_settings=None, stop_control=None,
                 runtime_errors=None, application_errors=None, origin_run_id=None):
        self.publisher = publisher
        self.origin_run_id = origin_run_id
        self.mode = mode
        self.stage = 'prepare'
        self.status = 'completed'
        self.failure = None
        self._suppress_terminal = False
        self._started_ns = None
        self._http_gap = False
        self.turns = TurnObserver(publisher)
        self.controls = ControlObserver(publisher)
        self.stop_control = stop_control if stop_control is not None else RunControl()
        self.context_operations = ContextOperationObserver(publisher)
        self.tool_progress = ToolProgressObserver(publisher, progress_settings)
        self.runtime_errors = runtime_errors if runtime_errors is not None else RuntimeErrorPolicy()
        self.application_errors = application_errors if application_errors is not None else ApplicationErrorPolicy()

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
    def bind(self, *, ainvoke_subagent=None, process_tasks=None, background_notices=None, alist_subagents=None, history_query=None, memory_query=None, task_queries=None, archive_root=None, project_id=None, execution_root=None):
        if self.mode == 'approval_resume':
            observe(self.controls, self.controls.submit_approval)
        services = RunServices(self.publisher, self.runtime_errors, self.stop_control,
                               self.turns, self.controls, self.context_operations, tool_progress=self.tool_progress,
                               ainvoke_subagent=ainvoke_subagent, process_tasks=process_tasks,
                               background_notices=background_notices, alist_subagents=alist_subagents, history_query=history_query, memory_query=memory_query, task_queries=task_queries, archive_root=archive_root, project_id=project_id, execution_root=execution_root)
        with bind_run_services(services), bind_http_run(self.report_http_gap):
            yield

    def report_http_gap(self):
        self._http_gap = True

    def start(self) -> None:
        self._started_ns = start_timer()
        emit_event(self.publisher, 'run_started', lambda: RunStartedData(self.mode, http_observation_version=1, origin_run_id=self.origin_run_id))
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
                duration_ms=elapsed_ms(self._started_ns),
                http_capture_status='incomplete' if self._http_gap else 'complete',
            ))
