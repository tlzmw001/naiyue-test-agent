"""A composed application; storage profiles share these domain APIs."""
from .lifetime import ApplicationLifetime
from .sessions.api import SessionAPI
from .sessions.run_api import RunAPI
from .sessions.task_api import TaskAPI
from .conversation.api import HistoryAPI
from .memory.api import MemoryAPI
from .observability.api import ObservationAPI
from .catalogs import ModelAPI, ToolAPI, SubagentAPI, McpAPI


class Application:
    def __init__(self, catalog, sessions, agent, registry, *, system_prompt, persistence, storage, usage, diagnostics, resources, memory, memory_jobs):
        owner = self._lifetime = ApplicationLifetime(catalog, sessions, resources)
        self.memory = MemoryAPI(owner, sessions, persistence, agent, memory, memory_jobs)
        sessions.memory = self.memory
        self.sessions = SessionAPI(owner, sessions, persistence, agent, storage.sessions)
        self.runs = RunAPI(owner, sessions, agent, persistence, storage.sessions, storage.conversation, storage.replies)
        self.history = HistoryAPI(owner, sessions, persistence, agent, catalog, registry, system_prompt,
                                  storage.sessions, storage.conversation, storage.subagents)
        self.tasks = TaskAPI(owner, sessions)
        self.observability = ObservationAPI(owner, sessions, storage.usage, storage.diagnostics, usage, diagnostics)
        self.models = ModelAPI(owner, catalog)
        self.tools = ToolAPI(owner, sessions, registry)
        self.subagents = SubagentAPI(owner, sessions)
        self.mcp = McpAPI(owner, sessions, registry)

    async def aclose(self):
        await self._lifetime.aclose()
