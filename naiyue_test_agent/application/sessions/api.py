"""SessionAPI operations with explicit dependencies and shared lifetime ownership."""
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking


class SessionAPI:
    def __init__(self, owner, sessions, persistence, agent, metadata):
        self._owner, self._sessions, self._persistence = owner, sessions, persistence
        self._agent, self._metadata = agent, metadata

    async def select_thinking(self, session_id, model_id, level):
        with self._owner.operation():
            await run_blocking(self._sessions.require(session_id).select_thinking, model_id, level)

    async def skills(self, workspace_root):
        with self._owner.operation():
            from naiyue_test_agent.application.sessions.skills import discover_project_skills
            return await run_blocking(discover_project_skills, workspace_root)

    def control_options(self):
        self._owner.check_open()
        return self._sessions.get_control_options()

    def active_tools(self, session_id):
        self._owner.check_open()
        return self._sessions.require(session_id).get_active_tools()

    async def select_tools(self, session_id, names):
        with self._owner.operation():
            await run_blocking(self._sessions.require(session_id).set_active_tools, names)

    def permission_mode(self, session_id):
        self._owner.check_open()
        return self._sessions.require(session_id).get_permission_mode()

    async def select_permission_mode(self, session_id, mode):
        with self._owner.operation():
            await run_blocking(self._sessions.require(session_id).set_permission_mode, mode)

    def is_loaded(self, session_id):
        return self._sessions.has_session(session_id)

    async def create(self, model_id):
        with self._owner.operation():
            return await run_blocking(self._sessions.create_session, model_id)

    async def create_workspace(self, model_id, workspace_root):
        with self._owner.operation():
            return await run_blocking(self._sessions.create_workspace_session, model_id, workspace_root)

    async def select_model(self, session_id, model_id):
        with self._owner.operation():
            return await run_blocking(self._sessions.require(session_id).select_model, model_id)

    async def prepare(self, session_id):
        with self._owner.operation():
            await await_settled(self._persistence.aprepare(session_id, self._agent))
            return await self.status(session_id)

    async def status(self, session_id):
        with self._owner.operation():
            return await self._persistence.astatus(session_id)

    async def update_presentation(self, session_id, **changes):
        with self._owner.operation():
            await run_blocking(self._metadata.update_presentation, session_id, **changes)

    async def reorder(self, workspace_root, identities):
        with self._owner.operation():
            await run_blocking(self._metadata.reorder, workspace_root, identities)

    async def delete(self, session_id):
        with self._owner.operation():
            await await_settled(self._persistence.adelete(session_id, self._agent))

    async def recover(self, session_id, *, checkpoint_id, action):
        with self._owner.operation():
            return await self._persistence.arecover(session_id, self._agent, checkpoint_id=checkpoint_id, action=action)

    async def restore(self, session_id):
        with self._owner.operation():
            return await self._persistence.arestore(session_id, self._agent)

    async def list(self):
        with self._owner.operation():
            return await run_blocking(self._metadata.list_sessions)

    async def list_workspace(self, workspace_root):
        with self._owner.operation():
            return await run_blocking(self._metadata.list_workspace_sessions, workspace_root)
