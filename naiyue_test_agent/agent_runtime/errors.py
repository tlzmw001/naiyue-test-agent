"""Runtime-owned failures and per-run error ports; no application imports."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.contracts.failures import (
    ErrorClassifier, FailureDescription, RunFailureData,
    normalize_classifiers, resolve_failure, safe_error_type,
    validate_failure_description, attach_failure_description,
)
from naiyue_test_agent.model_access.errors import describe_model_failure
from .run_control import UserStop
from naiyue_test_agent.tools.processes import ProcessCleanupError


logger = logging.getLogger(__name__)


RejectionReason = Literal[
    'stop_reason_error', 'invalid_tool_calls', 'missing_tool_calls', 'missing_ai_message',
    'missing_tool_call_id', 'duplicate_tool_call_ids', 'invalid_tool_call_ids',
]


class ModelResponseRejected(RuntimeError):
    """Response-rejection protocol; the runtime owns the rejection policy."""

    def __init__(self, reason: RejectionReason):
        self.reason = reason
        super().__init__(f'Model response rejected: {reason}')


class ContextCompactionError(RuntimeError):
    """A fixed, safe reason for a compaction that could not complete."""


class ToolBatchSchedulingError(RuntimeError):
    """The current tool batch cannot make safe scheduling progress."""


def _runtime_classification(error):
    if isinstance(error, TimeoutError):
        return 'transient', 'timeout'
    if isinstance(error, ConnectionError):
        return 'transient', 'connection_error'
    return None


@dataclass(frozen=True)
class RuntimeErrorPolicy:
    classifiers: tuple[ErrorClassifier, ...] = ()
    model_describer: Callable | None = None

    def __post_init__(self):
        object.__setattr__(self, 'classifiers', normalize_classifiers(self.classifiers))
        if self.model_describer is not None:
            normalize_classifiers((self.model_describer,))

    def describe(self, error, *, source='runtime', model_id=None):
        if isinstance(error, GraphBubbleUp) or (
                not isinstance(error, Exception)
                and not isinstance(error, (UserStop, asyncio.CancelledError))):
            return FailureDescription('runtime', 'unknown', 'unclassified', safe_error_type(error))
        protected = (
            ('cancelled', 'user_stop') if isinstance(error, UserStop) else
            ('cancelled', 'task_cancelled') if isinstance(error, asyncio.CancelledError) else
            ('tool_outcome_unknown', 'tool_outcome_unknown')
            if isinstance(error, ProcessCleanupError) else
            ('model_response_rejected', error.reason) if isinstance(error, ModelResponseRejected) else
            ('context_error', 'compaction_failed') if isinstance(error, ContextCompactionError) else
            ('runtime_error', 'tool_batch_scheduling_failed') if isinstance(error, ToolBatchSchedulingError) else None
        )
        if protected is not None:
            source = 'tool' if isinstance(error, ProcessCleanupError) else 'runtime'
            try:
                return FailureDescription(source, *protected, safe_error_type(error))
            except Exception as fault:
                try:
                    self._diagnostic(fault)
                except Exception as report_error:
                    logger.error('failure_diagnostic_report_failed error_type=%s', safe_error_type(report_error))
                return FailureDescription(source, 'unknown', 'unclassified', safe_error_type(error))
        if source == 'model':
            try:
                description = (self.model_describer(model_id, error) if self.model_describer
                               else describe_model_failure(error))
                return validate_failure_description(error, description, source='model')
            except Exception as fault:
                self._diagnostic(fault)
                return FailureDescription('model', 'unknown', 'unclassified', safe_error_type(error))
        return resolve_failure(error, source=source, classifiers=self.classifiers,
            builtin=_runtime_classification, report_diagnostic=self._diagnostic)

    @staticmethod
    def _diagnostic(error):
        logger.error('runtime_failure_diagnostics_unavailable error_type=%s', safe_error_type(error))


def describe_failure_in_context(error, context, *, source, model_id=None):
    policy = context.get('_failure_policy')
    if not isinstance(policy, RuntimeErrorPolicy):
        policy = RuntimeErrorPolicy()
    description = policy.describe(error, source=source, model_id=model_id)
    control = context.get('_run_control')
    if isinstance(error, UserStop) and control is not None and control.reason != 'user':
        description = FailureDescription('runtime', 'cancelled',
            {'failure': 'run_failure', 'close': 'application_close'}[control.reason], safe_error_type(error))
    attach_failure_description(error, description, run_id=context.get('run_id'))
    return description


def run_failure_data(description, *, stage='execute'):
    return RunFailureData(stage, description.category, description.reason, description.error_type)
