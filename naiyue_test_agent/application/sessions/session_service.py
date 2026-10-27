"""Multi-session registration, shared definitions and root run admission."""

from threading import Lock
from contextlib import ExitStack
from uuid import uuid4
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from langchain_core.messages import HumanMessage

from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer
from naiyue_test_agent.agent_runtime.tool_execution.async_batch import validate_tool_concurrency
from naiyue_test_agent.application.observability.run_events import ApplicationEventSubscriptions, RunObservation
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.registry import ToolRegistry
from naiyue_test_agent.contracts.failures import normalize_classifiers
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy
from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback
from naiyue_test_agent.application.observability.failure_policy import ApplicationErrorPolicy
from naiyue_test_agent.application.errors import SessionBusyError
from .agent_session import AgentSession, build_session
from .background_tasks import BackgroundProcessManager
from .task_queries import TaskQueries
from naiyue_test_agent.tools.subagent import subagent_selection


import logging
from pathlib import Path
from copy import deepcopy
from contextlib import contextmanager
from naiyue_test_agent.tools.subagent import SubAgentSpec
from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.tools.read import resolve_workspace_root
from naiyue_test_agent.execution_roots import ExecutionRoot
from .session_state import SessionState

logger = logging.getLogger('naiyue_test_agent.application')


class SessionService:
    """Locate sessions and coordinate admission; each session drives its run."""

    def __init__(self, catalog: ModelCatalog, tool_registry: ToolRegistry | None = None, *, initial_active_tools=None, permission_settings=None, progress_settings=None, max_active_runs=10, mcp_resources=None, http_attempt_limit=None, shell_task_settings=None, data_dir=None, project=None, admission=None, execution_authority=None, fixed_execution=False,environments=None,image_input_settings=None):
        if type(max_active_runs) is not int or max_active_runs <= 0:
            raise ValueError('Root run capacity must be a positive integer')
        from naiyue_test_agent.image_input_settings import ImageInputSettings
        self.image_input_settings = image_input_settings or ImageInputSettings()
        self._progress_settings = progress_settings
        from naiyue_test_agent.config_paths import get_data_dir, StorageSettings
        self._data_dir = (data_dir if data_dir is not None else get_data_dir(StorageSettings())).resolve()
        self._project = project
        self.environments=environments
        from .run_admission import RunAdmission
        self._admission = admission if admission is not None else RunAdmission(max_active_runs)
        self._execution_authority, self._fixed_execution = execution_authority, fixed_execution
        self.mcp_resources = mcp_resources
        self._permission_settings = permission_settings if permission_settings is not None else PermissionSettings()
        install_model_usage_callback(catalog, http_attempt_limit=http_attempt_limit)
        self._permission_reviewer = AutoPermissionReviewer.from_catalog(catalog, self._permission_settings.auto)
        self._catalog = catalog
        self.background_tasks = BackgroundProcessManager(shell_task_settings)
        self.task_queries = TaskQueries(self.background_tasks)
        self.memory = None
        self.memory_jobs = None
        self._tool_registry = tool_registry if tool_registry is not None else ToolRegistry()
        initial = self._tool_registry.names() if initial_active_tools is None else initial_active_tools
        self._initial_active_tools = tuple(spec.tool.name for spec in self._tool_registry.select(subagent_selection(initial)))
        self._sessions: dict[str, AgentSession] = {}
        self._subagents: dict[str, dict] = {}
        self._lock = Lock()
        self._admission.services.add(self)
        self._events = ApplicationEventSubscriptions()
        self._persistence = None
        self._assembled = False
        self._runtime_ready = False
        self._closing = False
        self._environment_management=set()
        self._system_prompt = None
        self._runtime_classifiers = ()
        self._application_classifiers = ()

    def require(self, session_id) -> AgentSession:
        """Borrow an already loaded owner; registry coordination stays here."""
        with self._lock:
            return self._sessions[session_id]

    def borrow_mcp(self, session_id, agent_path, server_id):
        """Internal resource access; tool authorization remains a separate boundary."""
        from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
        from naiyue_test_agent.mcp_client.resources import MCPResourceError
        session = self.require(session_id)
        if agent_path != 'root':
            if not session.is_busy():
                raise MCPResourceError('mcp_instance_not_active')
            try:
                status = session.subagents.subagent_status(agent_path)
            except KeyError:
                raise MCPResourceError('mcp_instance_not_active') from None
            if status['settled'] or status['status'] != 'running':
                raise MCPResourceError('mcp_instance_not_active')
        services = current_run_scope()
        if services and services.observation.publisher:
            source = getattr(services.observation.publisher, 'source', None)
            current_path = source.agent_path if source is not None else 'root'
            if (not services.observation.publisher.matches(session_id, services.observation.publisher.run_id)
                    or current_path != agent_path):
                raise MCPResourceError('mcp_scope_mismatch')
        if self.mcp_resources is None:
            raise MCPResourceError('mcp_server_not_enabled')
        return self.mcp_resources.borrow(session_id, agent_path, server_id,
                                         control=services.control if services else None)

    def bind_persistence(self, persistence):
        """Resolve the startup construction cycle once, before any sessions."""
        with self._lock:
            if self._persistence is not None or self._sessions or self._assembled:
                raise RuntimeError('Persistence must be bound once before sessions are loaded')
            self._persistence = persistence

    def assemble_runtime(self, checkpointer, *, system_prompt, response_format,
                         subagents=(), subagent_settings=None):
        from naiyue_test_agent.agent_runtime import _assemble_interactive_runtime
        from naiyue_test_agent.tools.settings import SubAgentOutputSettings
        with self._lock:
            self._assembled = True
            self._runtime_ready = False
        self.configure_subagent_runtime(checkpointer, subagent_settings or SubAgentOutputSettings())
        for spec in subagents:
            self.register_subagent(spec)
        agent = _assemble_interactive_runtime(self._catalog.get(self._catalog.default_model_id),
            tool_registry=self._tool_registry, session_port=self,
            permission_settings=self._permission_settings, permission_reviewer=self._permission_reviewer,
            system_prompt=system_prompt, response_format=response_format,
            checkpointer=checkpointer, observe_turns=True)
        with self._lock:
            self._system_prompt = system_prompt
            self._runtime_ready = True
        return agent

    def get_control_options(self):
        modes = ['Default', 'NoAsk', 'Auto', 'BypassApprovals']
        if self._permission_settings.auto is None:
            modes.remove('Auto')
        return {'default_active_tools': list(self._initial_active_tools),
                'default_permission_mode': self._permission_settings.mode, 'permission_modes': modes}

    def stop_loaded_sessions(self):
        # Creation/deletion IO may roll registry entries back while close drains
        # accepted operations. Retain actors, without routing their IDs again.
        with self._lock:
            self._closing = True
            sessions = tuple(self._sessions.values())
        for session in sessions:
            session.controls.request_scope_stop(reason='close')

    def session_archive_root(self, session_id):
        return (self._data_dir / 'workspaces' / self._project.project_id / 'artifacts' if self._project else
                self._data_dir / 'sessions' / session_id / 'artifacts')

    def workspace_archive_root(self, root):
        with self._lock:
            roots = {session._archive_root for session in self._sessions.values()
                     if session.snapshot_for_context()['workspace_root'] == root}
        if len(roots) != 1 or None in roots:
            raise ValueError('Archive scope is unavailable or ambiguous')
        return roots.pop()
    def output_guard(self, root):
        stack = ExitStack()
        with self._lock:
            owned = ({self.session_archive_root('')} if self._project else
                     {session._archive_root for session in self._sessions.values()})
            if root not in owned:raise ValueError('Archive scope does not belong to this host')
            if any(session.is_busy() and session._archive_root == root
                   for session in self._sessions.values()):
                raise ValueError('Workspace has an active run')
            stack.enter_context(self.background_tasks.output_guard(root))
        if self.environments is not None and self._project is not None:
            try:stack.enter_context(self.environments._operations.output_guard(self._project.project_id))
            except BaseException:
                stack.close()
                raise
        return stack
    def configure_error_classifiers(self, *, model_id=None, model=None, runtime=None, application=None):
        runtime = normalize_classifiers(runtime) if runtime is not None else None
        application = normalize_classifiers(application) if application is not None else None
        model = normalize_classifiers(model) if model is not None else None
        if (model is None) != (model_id is None):
            raise ValueError('Model classifiers require a model_id')
        with self._lock:
            if any(session.is_busy() for session in self._sessions.values()):
                raise SessionBusyError('Cannot change error mappings during an active run')
            if model is not None:
                self._catalog.configure_error_classifiers(model_id, model)
            if runtime is not None:
                self._runtime_classifiers = runtime
            if application is not None:
                self._application_classifiers = application

    def subscribe_events(self, handler, *, session_id=None):
        return self._events.subscribe(handler, session_id=session_id)

    def resolve_model(self, session_id: str, agent_path='root'):
        return self.require(session_id).resolve_model(agent_path)

    def resolve_request_configuration(self, session_id: str, agent_path='root'):
        return self.require(session_id).resolve_request_configuration(agent_path)

    def resolve_prompt_parts(self, session_id, agent_path='root'):
        if agent_path=='root' and self.memory_jobs is not None:self.memory_jobs.directory_attempt(session_id)
        return self.require(session_id).lifecycle.resolve_prompt_parts(agent_path)


    async def prepare_admission(self, session_id, *, stop=False):
        from naiyue_test_agent.execution_roots import validate_execution_root
        if not stop:self._admission.check_project(self._project.project_id if self._project else None)
        with self._lock:
            if session_id in self._environment_management:raise SessionBusyError('Session environment is being managed')
            owner = self._sessions[session_id]
        scope = owner.snapshot_for_context()
        facts = None
        root = scope['execution_root']
        access=None
        if not stop and scope['execution_binding']=='sandbox':
            from .execution_binding import resolve_capabilities
            row=await run_blocking(self._persistence.storage.sessions.get,session_id)
            access=await resolve_capabilities(self,row)
        elif not stop:
            root = await run_blocking(self.resolve_execution_root, root, scope['project_id'], scope['execution_binding'])
            if root is None and scope['project_id'] is not None:
                from naiyue_test_agent.execution_roots import ExecutionRootUnavailable
                raise ExecutionRootUnavailable('not_registered')
            facts = await run_blocking(validate_execution_root, root, scope['project_id'])
        return owner, scope, facts, root, access

    async def confirm_admission(self, prepared):
        owner, scope, facts, root, access = prepared
        if access is not None:
            from .execution_binding import check_sandbox
            row=await run_blocking(self._persistence.storage.sessions.get,owner.session_id)
            current=await run_blocking(check_sandbox,self,row)
            revision=await run_blocking(self.environments._service.projects.registry.execution_facts,scope['project_id'])
            if current.environment!=access.environment or revision.revision!=access.project_revision:
                from naiyue_test_agent.contracts.sandbox import SandboxError
                raise SandboxError('env_binding_mismatch')
        if facts is not None:
            current = await run_blocking(root.authority.validate, root, scope['project_id'])
            if current.revision != facts.revision:
                from naiyue_test_agent.execution_roots import ExecutionRootUnavailable
                raise ExecutionRootUnavailable('not_registered')

    def _admit(self, session_id, run_id, mode, *, decisions=None, remember_approved=None, stop=False,
               expected_conversation_revision=None, prepared_admission=None):
        with self._admission.lock:
            if not stop:self._admission.check_project(self._project.project_id if self._project else None)
            capacity_available = mode == 'approval_resume' and stop or self._admission.available()
            with self._lock:
                if self._closing:
                    raise RuntimeError('Cannot start a run while its host is closing')
                if session_id in self._environment_management:raise SessionBusyError('Session environment is being managed')
                session = self._sessions[session_id]
                if prepared_admission is not None:
                    owner, scope, facts, root, access = prepared_admission
                    if session is not owner or session.snapshot_for_context() != scope:
                        raise SessionBusyError('Session changed during execution admission')
                if self.background_tasks.is_cleaning(session._archive_root):
                    raise SessionBusyError('Workspace output cleanup is active')
                run_id = run_id or uuid4().hex
                observation = RunObservation(self._events.publisher(session_id, run_id), mode,
                    progress_settings=self._progress_settings,
                    runtime_errors=RuntimeErrorPolicy(self._runtime_classifiers, self._catalog.failure_describer()),
                    application_errors=ApplicationErrorPolicy(self._application_classifiers))
                run = session.lifecycle.admit_run(run_id, mode, observation, decisions=decisions,
                                  remember_approved=remember_approved, stop=stop,
                                  capacity_available=capacity_available, expected_conversation_revision=expected_conversation_revision,
                                  execution_root=prepared_admission[3] if prepared_admission is not None else None,
                                  execution_access=prepared_admission[4] if prepared_admission is not None else None)
        return session, run


    async def arun(self, agent, session_id, messages, *, run_id=None, max_concurrency=4, expected_conversation_revision=None, prepared_admission=None):
        validate_tool_concurrency(max_concurrency)
        if prepared_admission is None:
            prepared_admission = await self.prepare_admission(session_id)
            await self.confirm_admission(prepared_admission)
        session, run = self._admit(session_id, run_id, 'message', expected_conversation_revision=expected_conversation_revision, prepared_admission=prepared_admission)
        return await session.lifecycle.arun(agent, messages, run=run,
                                   max_concurrency=max_concurrency)

    async def acompact(self, agent, session_id, *, run_id=None, prepared_admission=None):
        with self._lock:
            if not self._runtime_ready:
                raise RuntimeError('Session runtime must be assembled before compaction')
        if prepared_admission is None:
            prepared_admission = await self.prepare_admission(session_id)
            await self.confirm_admission(prepared_admission)
        session, run = self._admit(session_id, run_id, 'compact', prepared_admission=prepared_admission)
        return await session.lifecycle.arun(agent, [], run=run,
                                   compact=True, system_prompt=self._system_prompt)

    async def aresume_tool_approval(self, agent, session_id, decisions, *, run_id=None, max_concurrency=4, remember_approved=None, stop=False, prepared_admission=None):
        validate_tool_concurrency(max_concurrency)
        if prepared_admission is None:
            prepared_admission = await self.prepare_admission(session_id, stop=stop)
            await self.confirm_admission(prepared_admission)
        session, run = self._admit(session_id, run_id, 'approval_resume',
            decisions=decisions, remember_approved=remember_approved, stop=stop, prepared_admission=prepared_admission)
        return await session.lifecycle.aresume_tool_approval(agent, decisions, run=run, max_concurrency=max_concurrency,
            remember_approved=remember_approved, stop=stop)

    def create_session(self, model_id: str) -> str:
        if self._project is not None and self._project.canonical_root is None:
            from naiyue_test_agent.contracts.projects import ProjectError
            raise ProjectError('project_execution_unavailable')
        root = self._project.canonical_root if self._fixed_execution and self._project else None
        return self._create_session(model_id, root)

    def create_workspace_session(self, model_id: str, workspace_root: str | Path) -> str:
        return self._create_session(model_id, resolve_workspace_root(workspace_root))

    def _create_session(self, model_id: str, workspace_root: Path | None,*,session_id=None,execution_setup=None) -> str:
        if self._fixed_execution and execution_setup is None:
            from naiyue_test_agent.contracts.projects import ProjectError
            expected = self._project.canonical_root if self._project else None
            if workspace_root != expected:
                raise ProjectError('project_execution_scope_mismatch')
        self._catalog.get(model_id)
        with self._lock:
            session_id = session_id or uuid4().hex
            if session_id in self._sessions:raise SessionPersistenceError('session_already_loaded')
            binding = 'sandbox' if execution_setup is not None else ('project_local' if self._execution_authority is not None else 'local') if workspace_root is not None else None
            state = SessionState(thread_id=uuid4().hex, model_id=model_id,
                execution_binding=binding,execution_setup=execution_setup,
                execution_root=self.bind_execution_root(ExecutionRoot(workspace_root, binding=binding)) if workspace_root is not None else None,
                last_execution_path=str(workspace_root) if workspace_root is not None else None,
                project_id=self._project.project_id if self._project else None, active_tool_names=self._initial_active_tools,
                permission_mode=self._permission_settings.mode)
            session = build_session(self, session_id, state)
            self._sessions[session_id] = session
        try:
            if self._persistence is not None:
                self._persistence.storage.sessions.create(session_id, {
                    'thread_id': session.get_thread_id(), 'model_id': model_id,
                    'workspace_root': workspace_root, 'project_id': state.project_id,
                    'execution_binding': state.execution_binding,
                    'execution_setup':execution_setup,
                    'execution_root_kind': state.execution_root.kind if state.execution_root else None, 'active_tool_names': session.get_active_tools(),
                    'permission_mode': session.get_permission_mode()})
        except BaseException:
            with self._lock:
                self._sessions.pop(session_id, None)
            raise
        return session_id

    def has_session(self, session_id):
        with self._lock:
            return session_id in self._sessions

    def loaded_session_ids(self):
        with self._lock:
            return tuple(self._sessions)

    def context_snapshot_is_current(self, session_id, snapshot):
        with self._lock:
            session = self._sessions.get(session_id)
            return session is not None and session.context_snapshot_is_current(snapshot)

    def bind_execution_root(self, root):
        from dataclasses import replace
        return replace(root, authority=self._execution_authority) if root is not None else None

    def saved_execution_root(self, row):
        from naiyue_test_agent.storage.execution_storage import root_from_row
        root = root_from_row(row)
        if row.get('execution_binding') == 'project_local':
            if self._project is None or self._project.project_id != row['project_id']:
                return None
            # Cache for display/preview only. Admission always resolves live facts.
            root = ExecutionRoot(self._project.canonical_root, binding='project_local')
        return self.bind_execution_root(root)

    def resolve_execution_root(self, root, project_id, binding=None):
        if binding == 'project_local' or root is not None and root.binding == 'project_local':
            if self._execution_authority is None or project_id is None:
                from naiyue_test_agent.execution_roots import ExecutionRootUnavailable
                raise ExecutionRootUnavailable('not_registered')
            return self._execution_authority.resolve(project_id)
        return root

    def install_restored_session(self, session_id, *, thread_id, model_id, thinking_level,
                                 workspace_root, active_tool_names, permission_mode, pending=None, conversation_revision=0, project_id=None, execution_root_kind=None, last_execution_path=None, execution_binding=None,execution_setup=None):
        with self._lock:
            if session_id in self._sessions:
                raise SessionPersistenceError('session_already_loaded')
            state = SessionState(thread_id=thread_id, model_id=model_id,
                thinking_level=thinking_level, project_id=project_id,
                last_execution_path=last_execution_path,
                execution_binding=execution_binding,execution_setup=execution_setup,
                execution_root=self.bind_execution_root(ExecutionRoot(workspace_root, execution_root_kind, binding=execution_binding or 'local')) if workspace_root is not None else None,
                active_tool_names=tuple(active_tool_names), permission_mode=permission_mode,
                pending_tool_approval=deepcopy(pending), conversation_revision=conversation_revision)
            self._sessions[session_id] = build_session(self, session_id, state)

    @contextmanager
    def manage_environment(self,session_id):
        with self._admission.lock:
            self._admission.check_project(self._project.project_id if self._project else None)
            with self._lock:
                actor=self._sessions.get(session_id)
                if self._closing or session_id in self._environment_management or actor is not None and actor.is_busy():
                    raise SessionBusyError('Session environment is busy')
                self._environment_management.add(session_id)
        try:yield
        finally:
            with self._lock:self._environment_management.discard(session_id)

    @contextmanager
    def deletion_guard(self, session_id):
        # Caller owns persistence's lock; reserve deletion before releasing this guard.
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and session.is_busy():
                raise SessionPersistenceError('session_recovery_blocked')
            yield

    def child_thread_ids(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
            return session.subagents.child_thread_ids() if session is not None else ()

    def remove_session(self, session_id):
        with self._lock:
            self._sessions.pop(session_id, None)

    def register_tool(self, spec):
        self._tool_registry.register(spec, permission_settings=self._permission_settings)

    def resolve_permission_context(self, session_id, agent_path='root'):
        return self.require(session_id).resolve_permission_context(agent_path)

    def resolve_active_tool_names(self, session_id, agent_path='root'):
        return self.require(session_id).get_active_tools(agent_path)

    def is_stop_requested(self, session_id: str) -> bool:
        return self.require(session_id).controls.is_stop_requested()

    def drain_steering(self, session_id: str) -> list[str]:
        return self.require(session_id).controls.drain_steering()

    def finish_run_boundary(self, session_id: str) -> tuple[bool, list[str]]:
        return self.require(session_id).controls.finish_run_boundary()

    def prepare_control_messages(self, session_id: str, messages: list[str]) -> list[HumanMessage]:
        return self.require(session_id).controls.prepare_control_messages(messages)

    def configure_subagent_runtime(self, checkpointer, result_settings):
        self._subagent_checkpointer = checkpointer
        self._subagent_result_settings = result_settings

    def register_subagent(self, spec):
        try:
            if not isinstance(spec, SubAgentSpec):
                raise ValueError('Subagent registration requires SubAgentSpec')
            self._tool_registry.select(spec.tool_names)
            if spec.model_id is not None:
                self._catalog.get(spec.model_id)
            with self._lock:
                if spec.name in self._subagents:
                    raise ValueError('Duplicate subagent name in registration')
            from naiyue_test_agent.agent_runtime.subagents import build_subagent_runtime
            agent = build_subagent_runtime(spec, catalog=self._catalog, tool_registry=self._tool_registry,
                checkpointer=self._subagent_checkpointer, session_port=self,
                permission_settings=self._permission_settings, permission_reviewer=self._permission_reviewer)
            with self._lock:
                if spec.name in self._subagents:
                    raise ValueError('Duplicate subagent name in registration')
                self._subagents[spec.name] = {'spec': spec, 'agent': agent}
        except Exception as error:
            logger.error('subagent_registration_failed error_type=%s', safe_error_type(error))
            raise

    def get_registered_subagents(self):
        with self._lock:
            return tuple(item['spec'] for item in self._subagents.values())

    async def ainvoke_subagent(self, agent, task, *, agent_id=None, parent_context, parent_state, parent_tool_call_id):
        with self._lock:
            session = self._sessions[parent_context.scope.identity.session_id]
            role = self._subagents.get(agent)
        return await session.subagents.ainvoke_subagent(agent, task, role=role, agent_id=agent_id, parent_context=parent_context,
            parent_state=parent_state, parent_tool_call_id=parent_tool_call_id)
