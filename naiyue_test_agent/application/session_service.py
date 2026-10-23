"""Multi-session registration, shared definitions and root run admission."""

from threading import Lock
from contextlib import ExitStack
from uuid import uuid4

from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer
from naiyue_test_agent.agent_runtime.tool_execution.async_batch import validate_tool_concurrency
from naiyue_test_agent.application.run_events import ApplicationEventSubscriptions, RunObservation
from naiyue_test_agent.application.session_controls import SessionControlMixin
from naiyue_test_agent.application.session_subagents import SessionSubagentControls
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.registry import ToolRegistry
from naiyue_test_agent.contracts.failures import normalize_classifiers
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy
from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback
from .failure_policy import ApplicationErrorPolicy
from .errors import SessionBusyError
from .agent_session import AgentSession
from .background_tasks import BackgroundProcessManager
from .task_queries import TaskQueries
from naiyue_test_agent.tools.subagent import subagent_selection


class SessionService(SessionControlMixin, SessionSubagentControls):
    """Locate sessions and coordinate admission; each session drives its run."""

    def __init__(self, catalog: ModelCatalog, tool_registry: ToolRegistry | None = None, *, initial_active_tools=None, permission_settings=None, progress_settings=None, max_active_runs=10, mcp_resources=None, http_attempt_limit=None, shell_task_settings=None, data_dir=None, project=None):
        if type(max_active_runs) is not int or max_active_runs <= 0:
            raise ValueError('Root run capacity must be a positive integer')
        self._max_active_runs = max_active_runs
        self._progress_settings = progress_settings
        from naiyue_test_agent.config_paths import get_data_dir, StorageSettings
        self._data_dir = (data_dir if data_dir is not None else get_data_dir(StorageSettings())).resolve()
        self._project = project
        self.mcp_resources = mcp_resources
        self._permission_settings = permission_settings if permission_settings is not None else PermissionSettings()
        install_model_usage_callback(catalog, http_attempt_limit=http_attempt_limit)
        self._permission_reviewer = AutoPermissionReviewer.from_catalog(catalog, self._permission_settings.auto)
        self._catalog = catalog
        self.background_tasks = BackgroundProcessManager(shell_task_settings)
        self.task_queries = TaskQueries(self.background_tasks)
        self.memory_jobs = None
        self._tool_registry = tool_registry if tool_registry is not None else ToolRegistry()
        initial = self._tool_registry.names() if initial_active_tools is None else initial_active_tools
        self._initial_active_tools = tuple(spec.tool.name for spec in self._tool_registry.select(subagent_selection(initial)))
        self._sessions: dict[str, AgentSession] = {}
        self._subagents: dict[str, dict] = {}
        self._lock = Lock()
        self._events = ApplicationEventSubscriptions()
        self._persistence = None
        self._assembled = False
        self._runtime_ready = False
        self._system_prompt = None
        self._runtime_classifiers = ()
        self._application_classifiers = ()

    def require(self, session_id) -> AgentSession:
        """Borrow an already loaded owner; registry coordination stays here."""
        with self._lock:
            return self._sessions[session_id]

    def borrow_mcp(self, session_id, agent_path, server_id):
        """Internal resource access; tool authorization remains a separate boundary."""
        from naiyue_test_agent.agent_runtime.run_services import current_run_services
        from naiyue_test_agent.mcp_client.resources import MCPResourceError
        session = self.require(session_id)
        if agent_path != 'root':
            if not session.is_busy():
                raise MCPResourceError('mcp_instance_not_active')
            try:
                status = session.subagent_status(agent_path)
            except KeyError:
                raise MCPResourceError('mcp_instance_not_active') from None
            if status['settled'] or status['status'] != 'running':
                raise MCPResourceError('mcp_instance_not_active')
        services = current_run_services()
        if services and services.publisher:
            source = getattr(services.publisher, 'source', None)
            current_path = source.agent_path if source is not None else 'root'
            if (not services.publisher.matches(session_id, services.publisher.run_id)
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
            sessions = tuple(self._sessions.values())
        for session in sessions:
            session.request_scope_stop(reason='close')

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
            if any(session.is_busy() and session._archive_root == root
                   for session in self._sessions.values()):
                raise ValueError('Workspace has an active run')
            stack.enter_context(self.background_tasks.output_guard(root))
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
        return self.require(session_id).resolve_prompt_parts(agent_path)


    def _admit(self, session_id, run_id, mode, *, decisions=None, remember_approved=None, stop=False, expected_conversation_revision=None):
        from naiyue_test_agent.execution_roots import validate_execution_root
        with self._lock:
            owner = self._sessions[session_id]
        scope = owner.snapshot_for_context()
        if not (mode == 'approval_resume' and stop):
            validate_execution_root(scope['execution_root'], scope['project_id'])
        with self._lock:
            session = self._sessions[session_id]
            if self.background_tasks.is_cleaning(session._archive_root):
                raise SessionBusyError('Workspace output cleanup is active')
            run_id = run_id or uuid4().hex
            observation = RunObservation(self._events.publisher(session_id, run_id), mode,
                progress_settings=self._progress_settings,
                runtime_errors=RuntimeErrorPolicy(self._runtime_classifiers, self._catalog.failure_describer()),
                application_errors=ApplicationErrorPolicy(self._application_classifiers))
            capacity_available = (mode == 'approval_resume' and stop or
                sum(actor.is_busy() for actor in self._sessions.values()) < self._max_active_runs)
            run = session.admit_run(run_id, mode, observation, decisions=decisions,
                              remember_approved=remember_approved, stop=stop,
                              capacity_available=capacity_available, expected_conversation_revision=expected_conversation_revision)
        return session, run


    async def arun(self, agent, session_id, messages, *, run_id=None, max_concurrency=4, expected_conversation_revision=None):
        validate_tool_concurrency(max_concurrency)
        session, run = self._admit(session_id, run_id, 'message', expected_conversation_revision=expected_conversation_revision)
        return await session.arun(agent, messages, run=run,
                                   max_concurrency=max_concurrency)

    async def acompact(self, agent, session_id, *, run_id=None):
        with self._lock:
            if not self._runtime_ready:
                raise RuntimeError('Session runtime must be assembled before compaction')
        session, run = self._admit(session_id, run_id, 'compact')
        return await session.arun(agent, [], run=run,
                                   compact=True, system_prompt=self._system_prompt)

    async def aresume_tool_approval(self, agent, session_id, decisions, *, run_id=None, max_concurrency=4, remember_approved=None, stop=False):
        validate_tool_concurrency(max_concurrency)
        session, run = self._admit(session_id, run_id, 'approval_resume',
            decisions=decisions, remember_approved=remember_approved, stop=stop)
        return await session.aresume_tool_approval(agent, decisions, run=run, max_concurrency=max_concurrency,
            remember_approved=remember_approved, stop=stop)
