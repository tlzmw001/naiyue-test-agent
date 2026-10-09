"""Small public errors; SDK errors and validation inputs remain server-private."""

from naiyue_test_agent.application import (
    SessionBusyError, SessionManualReviewRequiredError, SessionPendingApprovalError,
)
from naiyue_test_agent.application.session_storage import SessionPersistenceError
from naiyue_test_agent.model_access import ModelConfigError


class WebError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def public_error(error):
    if isinstance(error, WebError):
        return error
    if isinstance(error, SessionBusyError):
        return WebError('session_busy')
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
        return WebError(code if code in ('session_recovery_blocked', 'workspace_mismatch',
            'checkpoint_mismatch', 'session_save_unconfirmed') else 'session_unavailable')
    return WebError('operation_failed', 500)
