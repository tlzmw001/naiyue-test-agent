"""TaskAPI operations with explicit dependencies and shared lifetime ownership."""
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking


class TaskAPI:
    def __init__(self, owner, sessions, *, metadata=None):
        self._owner, self._sessions = owner, sessions
        self._metadata = metadata

    async def _queries(self,session_id):
        from naiyue_test_agent.contracts.session_environment import saved_setup
        from .task_queries import TaskQueries
        from naiyue_test_agent.contracts.sandbox import SandboxError
        queries=self._sessions.task_queries
        if self._metadata is None:return queries
        rows=([await run_blocking(self._metadata.get,session_id)] if session_id is not None else
              await run_blocking(self._metadata.list_sessions))
        others=[]
        for row in rows:
            if row.get('execution_binding')!='sandbox':continue
            setup=saved_setup(row)
            try:
                tasks=await self._sessions.environments.tasks(setup.binding_id,project_id=row['project_id'],session_id=row['session_id'])
            except SandboxError as error:
                if error.code!='env_not_registered':raise
            else:others.append(tasks)
        return TaskQueries(queries,others=others) if others else queries

    async def list(self, session_id=None, *, workspace_root=None, limit=20, before=None):
        with self._owner.operation():
            queries=await self._queries(session_id)
            return await queries.list(session_id=session_id,workspace_root=workspace_root,limit=limit,before=before)

    async def output(self, task_id, session_id=None, *, workspace_root=None):
        with self._owner.operation():
            queries=await self._queries(session_id)
            return await queries.output(task_id,session_id=session_id,workspace_root=workspace_root)

    async def stop(self, task_id, session_id=None, *, workspace_root=None):
        with self._owner.operation():
            queries=await self._queries(session_id)
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
