"""Run-bound memory operations using the application's existing resources."""
import logging

from langchain_core.tools import ToolException
from pydantic import ValidationError

from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.run_context import RunScope
from naiyue_test_agent.tools.memory import MemoryInput
from naiyue_test_agent.tools.permissions import require_execution_directory
from .memory_access import MemoryReadError


logger = logging.getLogger(__name__)


class MemoryAPI:
    def __init__(self, owner, sessions, persistence, agent, access, jobs):
        self._owner, self._sessions, self._persistence = owner, sessions, persistence
        self._agent, self._access, self._jobs = agent, access, jobs

    def available_for(self, project_id):
        return self._access is not None and (project_id is not None or self._access.personal_only)

    async def query(self, *, scope: RunScope, request: dict, tool_call_id: str | None = None) -> dict:
        with self._owner.operation():
            if self._access is None:
                raise ToolException('memory_unavailable: persistent memory is unavailable.')
            if not isinstance(scope, RunScope):
                raise ToolException('memory_scope_mismatch: the calling session or role is unavailable.')
            try:
                session = self._sessions.require(scope.identity.session_id)
            except KeyError as error:
                raise ToolException('memory_scope_mismatch: the calling session or role is unavailable.') from error
            origin_run_id = session.validate_memory_scope(scope)
            identity = scope.identity
            if scope.environment is not None and scope.environment.kind=='sandbox':
                await run_blocking(scope.capabilities.files.validate)
            else:
                require_execution_directory(identity.execution_root, identity.project_id)
            try:
                request = MemoryInput.model_validate(request).model_dump(mode='json')
            except ValidationError as error:
                raise ToolException('Invalid memory arguments. Use the action-specific schema.') from error
            try:
                if request['action'] == 'refresh_overview':
                    if self._jobs is None:
                        raise MemoryReadError('memory_unavailable')
                    model_id, model = session.resolve_model()
                    return await self._jobs.request(source_session_id=identity.session_id,
                        target_session_id=request['target_session_id'] or identity.session_id,
                        workspace_root=identity.execution_path, origin_run_id=origin_run_id,
                        model_id=model_id, model=model, tool_call_id=tool_call_id,
                        start_sequence=request['start_sequence'], end_sequence=request['end_sequence'])
                if request['action'] in ('record', 'update', 'disable', 'forget'):
                    async with self._persistence.history_lock(identity.session_id):
                        await self._persistence.save_memory_sources(identity.session_id, self._agent)
                        return await run_blocking(self._access.query, identity.session_id, identity.project_id, request)
                return await run_blocking(self._access.query, identity.session_id, identity.project_id, request)
            except MemoryReadError as error:
                logger.warning('memory_query_failed error_type=%s', type(error).__name__)
                raise ToolException(f'Memory query failed ({error}).') from error
