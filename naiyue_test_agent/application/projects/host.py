"""Project-owned domains and resources; no model or application registry ownership."""
from contextlib import ExitStack

from naiyue_test_agent.application.lifetime import ApplicationLifetime
from naiyue_test_agent.application.sessions.api import SessionAPI
from naiyue_test_agent.application.sessions.run_api import RunAPI
from naiyue_test_agent.application.sessions.task_api import TaskAPI
from naiyue_test_agent.application.conversation.api import HistoryAPI
from naiyue_test_agent.application.memory.api import MemoryAPI
from naiyue_test_agent.application.observability.api import ObservationAPI
from naiyue_test_agent.application.catalogs import ToolAPI, SubagentAPI, McpAPI
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.agent_runtime.async_utils import run_blocking


class ProjectHost:
    def __init__(self, catalog, sessions, agent, registry, *, system_prompt, persistence,
                 storage, usage, diagnostics, resources, memory, memory_jobs, parent):
        self.project_id = sessions._project.project_id if sessions._project else None
        owner = self._lifetime = ApplicationLifetime(None, sessions, resources, parent=parent)
        self.memory = MemoryAPI(owner, sessions, persistence, agent, memory, memory_jobs)
        sessions.memory = self.memory
        self.sessions = SessionAPI(owner, sessions, persistence, agent, storage.sessions)
        self.runs = RunAPI(owner, sessions, agent, persistence, storage.sessions, storage.conversation, storage.replies)
        self.history = HistoryAPI(owner, sessions, persistence, agent, catalog, registry, system_prompt,
                                  storage.sessions, storage.conversation, storage.subagents)
        self.tasks = TaskAPI(owner, sessions, metadata=storage.sessions)
        self.observability = ObservationAPI(owner, sessions, storage.usage, storage.diagnostics,
                                             usage, diagnostics, metadata=storage.sessions)
        self.tools = ToolAPI(owner, sessions, registry)
        self.subagents = SubagentAPI(owner, sessions)
        self.mcp = McpAPI(owner, sessions, registry)

    def _check_idle(self):
        owner, sessions = self._lifetime, self._lifetime._sessions
        if owner._operations or owner._tasks:
            raise ProjectError('project_in_use')
        persistence = self.sessions._persistence
        with persistence._lock:
            if persistence._active or persistence._recovering or persistence._blocked:
                raise ProjectError('project_in_use')
        with ExitStack() as guards:
            for sid in sessions.loaded_session_ids():
                state = sessions.require(sid).describe_session()
                if state['busy'] or state['pending_approval'] or state['manual_review_required']:
                    raise ProjectError('project_in_use')
                guards.enter_context(sessions.background_tasks.deletion_guard(sid))
                if sessions.memory_jobs:
                    guards.enter_context(sessions.memory_jobs.deletion_guard(sid))

    async def _close(self):
        self._lifetime.check_open()
        try:
            self._check_idle()
            rows = await run_blocking(self.sessions._metadata.list_sessions)
            if any(row['state'] in ('running', 'interrupted', 'review_required') for row in rows):
                raise ProjectError('project_in_use')
            self._check_idle()
        except ProjectError:
            raise
        except Exception as error:
            raise ProjectError('project_in_use') from error
        await self._lifetime.aclose()
