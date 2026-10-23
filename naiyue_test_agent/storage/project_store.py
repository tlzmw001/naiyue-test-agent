"""Resource composition; domain operations live on their repositories."""
from .database import Database
from .sessions import SessionRepo
from .conversation import ConversationRepo
from .replies import ReplyRepo
from .subagents import SubagentRepo
from .usage import UsageRepo
from .diagnostics import DiagnosticRepo
from .memory import MemoryRepo
from .unit_of_work import RunUnitOfWork


class ProjectStore:
    def __init__(self, path):
        self.database = Database(path)
        self.path = self.database.path
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
