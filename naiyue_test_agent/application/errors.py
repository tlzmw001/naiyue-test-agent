"""Application admission errors; database failures are defined in storage.errors."""


class SessionBusyError(RuntimeError):
    """A session already owns an active run."""


class RunCapacityExceededError(RuntimeError):
    """The application has no free root run slot; callers may resubmit later."""


class SessionPendingApprovalError(RuntimeError):
    """A session's approval state does not permit this operation."""


class SessionApprovalExpiredError(SessionPendingApprovalError):
    """The reviewed project revision no longer describes the execution target."""


class SessionManualReviewRequiredError(RuntimeError):
    """An uncertain side effect must be reviewed before continuing."""
