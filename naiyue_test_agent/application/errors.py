"""Application-owned admission and persistence errors, independent of model SDKs."""


class SessionBusyError(RuntimeError):
    """A session already owns an active run."""


class RunCapacityExceededError(RuntimeError):
    """The application has no free root run slot; callers may resubmit later."""


class SessionPendingApprovalError(RuntimeError):
    """A session's approval state does not permit this operation."""


class SessionManualReviewRequiredError(RuntimeError):
    """An uncertain side effect must be reviewed before continuing."""


class SessionPersistenceError(RuntimeError):
    """A session could not be stored or safely recovered."""


class StorageStartupError(SessionPersistenceError):
    """Finite startup failure facts; the original exception remains the cause."""

    def __init__(self, code, *, database_path, phase, target_schema, source_schema=None,
                 current_schema=None, backup_id=None):
        super().__init__(code)
        self.database_path = database_path
        self.phase = phase
        self.source_schema = source_schema
        self.current_schema = current_schema
        self.target_schema = target_schema
        self.backup_id = backup_id
