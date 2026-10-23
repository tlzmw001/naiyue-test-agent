"""Application domain mappings; runtime reports are consumed, not reclassified as SDK errors."""

import logging
from dataclasses import dataclass

from naiyue_test_agent.contracts.failures import FailureDescription, normalize_classifiers, resolve_failure, safe_error_type
from naiyue_test_agent.application.errors import SessionBusyError, SessionPendingApprovalError, SessionManualReviewRequiredError, SessionPersistenceError
from naiyue_test_agent.application.sessions.project_instructions import ProjectInstructionsError


logger = logging.getLogger(__name__)
_PERSISTENCE_REASONS = frozenset((
    'session_save_unconfirmed', 'session_recovery_blocked', 'workspace_mismatch',
    'checkpoint_mismatch', 'checkpoint_not_safely_finished',
))


def _application_classification(error):
    if isinstance(error, SessionPersistenceError):
        reason = error.args[0] if error.args else None
        return 'persistence_error', reason if isinstance(reason, str) and reason in _PERSISTENCE_REASONS else 'session_unavailable'
    if isinstance(error, SessionManualReviewRequiredError):
        return 'tool_outcome_unknown', 'manual_review_required'
    if isinstance(error, SessionBusyError):
        return 'request_rejected', 'session_busy'
    if isinstance(error, SessionPendingApprovalError):
        return 'request_rejected', 'approval_required'
    if isinstance(error, ProjectInstructionsError):
        return 'request_rejected', 'project_instructions_unavailable'
    return None


@dataclass(frozen=True)
class ApplicationErrorPolicy:
    classifiers: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'classifiers', normalize_classifiers(self.classifiers))

    def try_describe(self, error):
        return resolve_failure(error, source='application', classifiers=self.classifiers,
            builtin=_application_classification, fallback=False, report_diagnostic=lambda fault: logger.error(
                'run_failure_diagnostics_unavailable error_type=%s', safe_error_type(fault)))

    def describe(self, error):
        description = self.try_describe(error)
        return (description if description is not None else
                FailureDescription('application', 'unknown', 'unclassified', safe_error_type(error)))
