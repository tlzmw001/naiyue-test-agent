"""SessionAPI operations with explicit dependencies and shared lifetime ownership."""
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking


class SessionAPI:
    def __init__(self, owner, sessions, persistence, agent, metadata):
        self._owner, self._sessions, self._persistence = owner, sessions, persistence
        self._agent, self._metadata = agent, metadata

    async def select_thinking(self, session_id, model_id, level):
        with self._owner.operation():
            await run_blocking(self._sessions.require(session_id).select_thinking, model_id, level)

    async def skills(self, workspace_root, *, session_id=None):
        with self._owner.operation():
            from naiyue_test_agent.application.sessions.skills import discover_project_skills
            from naiyue_test_agent.execution.host_files import HostFileSystem
            from naiyue_test_agent.execution_roots import ExecutionRoot, ExecutionRootUnavailable
            from naiyue_test_agent.tools.read import resolve_workspace_root
            if session_id is not None:
                from .execution_binding import resolve_capabilities
                row=await run_blocking(self._metadata.get,session_id)
                access=await resolve_capabilities(self._sessions,row)
                return await run_blocking(discover_project_skills,access.files)
            def discover():
                if workspace_root is None:
                    return discover_project_skills(None)
                root = resolve_workspace_root(workspace_root)
                project = self._sessions._project
                if self._sessions._execution_authority is not None:
                    execution = self._sessions.resolve_execution_root(None, project.project_id, 'project_local')
                    if execution.path != root:
                        raise ExecutionRootUnavailable('wrong_project')
                else:
                    execution = ExecutionRoot(root)
                files = HostFileSystem(execution, project.project_id if project is not None else None)
                return discover_project_skills(files)
            return await run_blocking(discover)

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
        self._owner.check_open()
        return self._sessions.has_session(session_id)

    async def create(self, model_id):
        with self._owner.operation():
            return await run_blocking(self._sessions.create_session, model_id)

    async def create_sandbox(self,model_id,*,request_id,include_untracked=(),profile_id=None,expected_profile_revision=None):
        from .execution_binding import create_sandbox_session
        with self._owner.operation():
            return await create_sandbox_session(self._sessions,model_id,request_id,include_untracked,
                profile_id=profile_id,expected_profile_revision=expected_profile_revision)

    async def result_options(self,session_id):
        from .result_api import result_call
        return await result_call(self,session_id,'result_options')

    async def capture_result(self,session_id,*,result_id,include_untracked):
        from .result_api import result_call
        return await result_call(self,session_id,'capture_result',result_id=result_id,include_untracked=include_untracked)

    async def results(self,session_id):
        from .result_api import result_call
        return await result_call(self,session_id,'results')

    async def result(self,session_id,result_id):
        from .result_api import result_call
        return await result_call(self,session_id,'result',result_id)

    async def result_diff(self,session_id,result_id,path):
        from .result_api import result_call
        return await result_call(self,session_id,'result_diff',result_id,path)

    async def delivery_options(self,session_id,result_id):
        from .result_api import result_call
        return await result_call(self,session_id,'delivery_options',result_id)

    async def deliver(self,session_id,result_id,command):
        from .result_api import result_call
        return await result_call(self,session_id,'deliver',result_id,command)

    async def delivery_operations(self,session_id,result_id):
        from .result_api import result_call
        return await result_call(self,session_id,'delivery_operations',result_id)

    async def acknowledge_delivery(self,session_id,result_id,operation_id):
        from .result_api import result_call
        return await result_call(self,session_id,'acknowledge_delivery',result_id,operation_id)

    async def prepare_environment(self,session_id):
        from .execution_binding import prepare_sandbox_session
        with self._owner.operation():
            async with self._persistence.history_lock(session_id):
                return await prepare_sandbox_session(self._sessions,session_id)

    async def environment_action(self,session_id,action):
        from .execution_binding import environment_action
        with self._owner.operation():
            async with self._persistence.history_lock(session_id):
                status=await self.status(session_id)
                if status['state']=='running':
                    from naiyue_test_agent.application.errors import SessionBusyError
                    raise SessionBusyError('Session is busy')
                with self._sessions.manage_environment(session_id):
                    return await environment_action(self._sessions,session_id,action)

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
