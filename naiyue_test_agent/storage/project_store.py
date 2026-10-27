"""Resource composition; domain operations live on their repositories."""
from .database import Database
from naiyue_test_agent.config_paths import DEFAULT_BUSY_TIMEOUT_MS
from .sessions import SessionRepo
from .conversation import ConversationRepo
from .replies import ReplyRepo
from .subagents import SubagentRepo
from .usage import UsageRepo
from .diagnostics import DiagnosticRepo
from .memory import MemoryRepo
from .unit_of_work import RunUnitOfWork


class ProjectStore:
    def __init__(self, path, *, require_existing=False, runtime_profile=None, busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS):
        self._assemble(Database(path, require_existing=require_existing, runtime_profile=runtime_profile,
                                busy_timeout_ms=busy_timeout_ms))

    @classmethod
    def open_reserved(cls, project, expected_file=None, *, busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS):
        import os
        exists = os.path.lexists(project.database_path)
        store = cls.__new__(cls)
        store._assemble(Database(project.database_path, require_existing=exists, create_new=not exists,
                                 reserved_project=project, expected_file=expected_file, busy_timeout_ms=busy_timeout_ms))
        return store

    @classmethod
    def in_memory(cls, *, busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS):
        store = cls.__new__(cls)
        store._assemble(Database.open_memory(busy_timeout_ms=busy_timeout_ms))
        return store

    def _assemble(self, database):
        self.database = database
        self.path = database.path
        self.sessions = SessionRepo(self.database)
        self.conversation = ConversationRepo(self.database)
        self.replies = ReplyRepo(self.database)
        self.subagents = SubagentRepo(self.database)
        self.usage = UsageRepo(self.database)
        self.diagnostics = DiagnosticRepo(self.database)
        self.memory = MemoryRepo(self.database)
        self.runs = RunUnitOfWork(self.database)

    def close(self):
        self.database.close()

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        return self.database.__exit__(error_type, error, traceback)
