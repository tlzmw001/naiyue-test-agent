"""Storage failures shared by repositories and their callers."""


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
