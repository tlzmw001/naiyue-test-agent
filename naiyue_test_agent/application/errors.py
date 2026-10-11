"""Application-owned admission and persistence errors, independent of model SDKs."""


class SessionBusyError(RuntimeError):
    """A session already owns an active run."""


class SessionPendingApprovalError(RuntimeError):
    """A session's approval state does not permit this operation."""


class SessionManualReviewRequiredError(RuntimeError):
    """An uncertain side effect must be reviewed before continuing."""


class SessionPersistenceError(RuntimeError):
    """A session could not be stored or safely recovered."""
