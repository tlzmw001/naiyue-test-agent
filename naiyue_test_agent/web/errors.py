"""Small public errors; SDK errors and validation inputs remain server-private."""

from naiyue_test_agent.application import (
    RunCapacityExceededError, SessionBusyError, SessionManualReviewRequiredError, SessionPendingApprovalError,
)
from naiyue_test_agent.application.session_storage import SessionPersistenceError
from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.execution_roots import ExecutionRootUnavailable


class WebError(Exception):
    def __init__(self, code, status=409, *, reason=None):
        self.code, self.status, self.reason = code, status, reason
        super().__init__(code)


def public_error(error):
    if isinstance(error, WebError):
        return error
    if isinstance(error, ExecutionRootUnavailable):
        return WebError(error.code, reason=error.reason)
    if isinstance(error, SessionBusyError):
        return WebError('session_busy')
    if isinstance(error, RunCapacityExceededError):
        return WebError('run_capacity_exceeded')
    if isinstance(error, SessionManualReviewRequiredError):
        return WebError('manual_review_required')
    if isinstance(error, SessionPendingApprovalError):
        return WebError('approval_required')
    if isinstance(error, ModelConfigError):
        return WebError('model_unavailable', 422)
    if isinstance(error, KeyError):
        return WebError('session_not_found', 404)
    if isinstance(error, SessionPersistenceError):
        code = error.args[0] if error.args else ''
        if code in ('session_not_found', 'history_message_not_found'):
            return WebError(code, 404)
        return WebError(code if code in ('session_recovery_blocked', 'session_background_tasks_active', 'workspace_mismatch',
            'checkpoint_mismatch', 'session_save_unconfirmed', 'approval_restore_unavailable',
            'session_delete_unconfirmed', 'history_cursor_stale', 'history_invalid_offset') else
            'session_unavailable')
    return WebError('operation_failed', 500)
