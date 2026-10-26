"""One session owns its state and composes explicit execution collaborators."""
from copy import deepcopy
from threading import Lock
from langchain_core.tools import ToolException
from naiyue_test_agent.contracts.run_context import RunIdentity

from .session_state import SessionState, SessionRuntime
from .session_controls import ControlQueue
from .session_approval import ApprovalBook
from .session_execution import RunLifecycle
from naiyue_test_agent.application.subagents.session_subagents import SubagentRegistry


class AgentSession:
    def __init__(self, session_id, state: SessionState, *, catalog, tool_registry,
                 permission_settings, persistence=None, invoke_subagent=None, registered_subagents=None,
                 progress_settings=None, subagent_result_settings=None, mcp_resources=None, process_tasks=None, archive_root=None, memory=None):
        self.session_id = session_id
        self._runtime = SessionRuntime(session_id, state)
        self._catalog, self._tool_registry = catalog, tool_registry
        self._permission_settings, self._persistence = permission_settings, persistence
        self._archive_root = archive_root
        self._model_selection_lock = Lock()
        self._tool_selection_lock = Lock()
        self._permission_selection_lock = Lock()
        self.controls = ControlQueue(self._runtime)
        self.approvals = ApprovalBook(self._runtime)
        self.subagents = SubagentRegistry(self._runtime, controls=self.controls, approvals=self.approvals,
            catalog=catalog, persistence=persistence, registered_subagents=registered_subagents,
            progress_settings=progress_settings, result_settings=subagent_result_settings,
            mcp_resources=mcp_resources, archive_root=archive_root, memory=memory)
        self.lifecycle = RunLifecycle(self._runtime, controls=self.controls, approvals=self.approvals,
            subagents=self.subagents, tool_registry=tool_registry, persistence=persistence,
            registered_subagents=registered_subagents, invoke_subagent=invoke_subagent,
            process_tasks=process_tasks, archive_root=archive_root, mcp_resources=mcp_resources,
            resolve_model=self.resolve_model, active_tools=self.get_active_tools, memory=memory)
        if mcp_resources is not None:
            mcp_resources.set_session_occupied(session_id, state.pending_tool_approval is not None)

    def describe_session(self):
        with self._runtime.lock:
            row = self._runtime.state
            return {'session_id': self.session_id, 'conversation_revision': row.conversation_revision, 'model_id': row.model_id,
                'thinking_level': row.thinking_level, 'project_id': row.project_id,
                'execution_root_kind': row.execution_root.kind if row.execution_root else None,
                'active_tools': list(row.active_tool_names), 'permission_mode': row.permission_mode,
                'workspace_root': str(row.execution_path) if row.execution_path else None,
                'busy': (self._runtime.run is not None), 'run_id': (self._runtime.run.run_id if self._runtime.run else None),
                'manual_review_required': row.manual_review_required,
                'pending_approval': row.pending_tool_approval is not None,
                'approval_run_id': (row.pending_tool_approval.get('requested_run_id')
                    if row.pending_tool_approval is not None else None)}

    def validate_memory_scope(self, scope):
        """Validate one live root/child identity under the shared state lock."""
        with self._runtime.lock:
            run = self._runtime.run
            if run is not None and run.observation.stage == 'execute':
                identity = scope.identity
                if identity.agent_path == 'root':
                    expected = RunIdentity(self.session_id, run.run_id, project_id=run.project_id,
                                           execution_root=run.execution_root)
                    publisher, names = run.observation.publisher, self._runtime.state.active_tool_names
                    live = True
                else:
                    child = run.subagent_instances.get(identity.agent_path)
                    if child is None:
                        raise ToolException('memory_scope_mismatch: the calling session or role is unavailable.')
                    expected = RunIdentity(self.session_id, child['run_id'], child['agent_path'],
                        child['project_id'], child['execution_root'], child['parent_tool_call_id'],
                        child['parent_run_id'], child['root_run_id'])
                    publisher, names = child.get('publisher_view'), child['tool_names']
                    live = child['phase'] == 'execute' and not child['settled']
                bound=(run.files is None or scope.environment==run.files.environment and
                    (run.files.environment.kind!='sandbox' or scope.capabilities.files is run.files))
                if (live and identity == expected and bound
                        and publisher is not None and scope.observation.publisher is publisher and 'memory' in names):
                    return run.run_id
        raise ToolException('memory_scope_mismatch: the calling session or role is unavailable.')

    def get_thread_id(self):
        with self._runtime.lock:
            return self._runtime.state.thread_id

    def is_busy(self):
        with self._runtime.lock:
            return self._runtime.run is not None

    def snapshot_for_context(self):
        with self._runtime.lock:
            row = self._runtime.state
            return {'thread_id': row.thread_id, 'model_id': row.model_id,
                'workspace_root': row.execution_path, 'project_id': row.project_id,
                'execution_root': row.execution_root, 'active_tool_names': row.active_tool_names,
                'last_execution_path': row.last_execution_path,
                'execution_binding': row.execution_binding,
                'execution_setup':deepcopy(row.execution_setup),
                'busy': (self._runtime.run is not None), 'pending_approval': row.pending_tool_approval is not None,
                'manual_review_required': row.manual_review_required}

    def context_snapshot_is_current(self, snapshot):
        with self._runtime.lock:
            row = self._runtime.state
            return (self._runtime.run is None and not row.pending_tool_approval and not row.manual_review_required
                and row.thread_id == snapshot['thread_id'] and row.model_id == snapshot['model_id']
                and row.active_tool_names == snapshot['active_tool_names'])

    def approval_seed_identity(self):
        with self._runtime.lock:
            row = self._runtime.state
            return ((row.thread_id, row.pending_tool_approval['batch_id'])
                    if row.pending_tool_approval is not None else None)

    def select_model(self, model_id):
        self._catalog.get(model_id)
        with self._model_selection_lock:
            if self._persistence is not None:
                self._persistence.storage.sessions.select_model(self.session_id, model_id)
            with self._runtime.lock:
                self._runtime.state.model_id = model_id
                self._runtime.state.thinking_level = 'model_default'

    def select_thinking(self, model_id, level):
        self._catalog.with_thinking(model_id, level)
        with self._model_selection_lock:
            with self._runtime.lock:
                if self._runtime.state.model_id != model_id:
                    raise ValueError('Model selection changed')
            if self._persistence is not None:
                self._persistence.storage.sessions.select_thinking(self.session_id, level)
            with self._runtime.lock:
                self._runtime.state.thinking_level = level

    def set_active_tools(self, names):
        from naiyue_test_agent.tools.subagent import subagent_selection
        names = subagent_selection(names)
        names = tuple(spec.tool.name for spec in self._tool_registry.select(names))
        with self._tool_selection_lock:
            if self._persistence is not None:
                self._persistence.storage.sessions.select_active_tools(self.session_id, names)
            with self._runtime.lock:
                self._runtime.state.active_tool_names = names

    def set_permission_mode(self, mode):
        self._permission_settings.validate_mode(mode)
        with self._permission_selection_lock:
            if self._persistence is not None:
                self._persistence.storage.sessions.select_permission_mode(self.session_id, mode)
            with self._runtime.lock:
                self._runtime.state.permission_mode = mode

    def resolve_model(self, agent_path='root'):
        with self._runtime.lock:
            if agent_path == 'root':
                identity, level = self._runtime.state.model_id, self._runtime.state.thinking_level
            else:
                identity, level = self._runtime.run.subagent_instances[agent_path]['model_id'], 'model_default'
        return identity, self._catalog.with_thinking(identity, level)

    def resolve_request_configuration(self, agent_path='root'):
        with self._runtime.lock:
            if agent_path == 'root':
                row = self._runtime.state
                identity, level, names = row.model_id, row.thinking_level, row.active_tool_names
            else:
                instance = self._runtime.run.subagent_instances[agent_path]
                identity, level, names = instance['model_id'], 'model_default', instance['tool_names']
        return identity, self._catalog.with_thinking(identity, level), tuple(names)

    def get_active_tools(self, agent_path='root'):
        with self._runtime.lock:
            return (self._runtime.state.active_tool_names if agent_path == 'root'
                else self._runtime.run.subagent_instances[agent_path]['tool_names'])

    def get_permission_mode(self):
        with self._runtime.lock:
            return self._runtime.state.permission_mode

    def resolve_permission_context(self, agent_path='root'):
        with self._runtime.lock:
            if agent_path != 'root':
                self._runtime.run.subagent_instances[agent_path]
            return {'mode': self._runtime.state.permission_mode,
                'session_allow': deepcopy(self._runtime.state.session_allow),
                'project_instructions': (self._runtime.run.project_instructions if self._runtime.run else None)}
