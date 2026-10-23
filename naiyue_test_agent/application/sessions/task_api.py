"""TaskAPI operations with explicit dependencies and shared lifetime ownership."""
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking


class TaskAPI:
    def __init__(self, owner, sessions):
        self._owner, self._sessions = owner, sessions

    async def list(self, session_id=None, *, workspace_root=None, limit=20, before=None):
        with self._owner.operation():
            return await self._sessions.task_queries.list(session_id=session_id, workspace_root=workspace_root, limit=limit, before=before)

    async def output(self, task_id, session_id=None, *, workspace_root=None):
        with self._owner.operation():
            return await self._sessions.task_queries.output(task_id, session_id=session_id, workspace_root=workspace_root)

    async def stop(self, task_id, session_id=None, *, workspace_root=None):
        with self._owner.operation():
            return await self._sessions.task_queries.stop(task_id, session_id=session_id, workspace_root=workspace_root)

    async def preview_outputs(self, root):
        from naiyue_test_agent.tools.output import preview_shell_outputs
        with self._owner.operation():
            archive = self._sessions.workspace_archive_root(root)
            with self._sessions.output_guard(archive):
                return await run_blocking(preview_shell_outputs, archive)

    async def clear_outputs(self, plan):
        from naiyue_test_agent.tools.output import clear_shell_outputs
        with self._owner.operation(), self._sessions.output_guard(plan.root):
            return await run_blocking(clear_shell_outputs, plan)
