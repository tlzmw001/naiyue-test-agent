"""TaskAPI operations with explicit dependencies and shared lifetime ownership."""
from naiyue_test_agent.agent_runtime.async_utils import run_blocking


class TaskAPI:
    def __init__(self, owner, sessions, *, metadata):
        self._owner, self._sessions = owner, sessions
        self._metadata = metadata

    async def _check_session(self, session_id):
        if session_id is not None:
            await run_blocking(self._metadata.get, session_id)

    async def list(self, session_id=None, *, workspace_root=None, limit=20, before=None):
        with self._owner.operation():
            await self._check_session(session_id)
            queries=self._sessions.task_queries
            return await queries.list(session_id=session_id,workspace_root=workspace_root,limit=limit,before=before)

    async def output(self, task_id, session_id=None, *, workspace_root=None):
        with self._owner.operation():
            await self._check_session(session_id)
            queries=self._sessions.task_queries
            return await queries.output(task_id,session_id=session_id,workspace_root=workspace_root)

    async def stop(self, task_id, session_id=None, *, workspace_root=None):
        with self._owner.operation():
            await self._check_session(session_id)
            queries=self._sessions.task_queries
            return await queries.stop(task_id,session_id=session_id,workspace_root=workspace_root)

    async def preview_outputs(self, root):
        from naiyue_test_agent.tools.output import preview_shell_outputs
        with self._owner.operation():
            archive = self._sessions.workspace_archive_root(root)
            def preview():
                with self._sessions.output_guard(archive):
                    return preview_shell_outputs(archive)
            return await run_blocking(preview)

    async def clear_outputs(self, plan):
        from naiyue_test_agent.tools.output import clear_shell_outputs
        with self._owner.operation():
            def clear():
                with self._sessions.output_guard(plan.root):
                    return clear_shell_outputs(plan)
            return await run_blocking(clear)
