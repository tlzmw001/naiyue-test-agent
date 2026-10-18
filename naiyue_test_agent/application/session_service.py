"""Multi-session registration, shared definitions and root run admission."""

from threading import Lock
from uuid import uuid4

from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer
from naiyue_test_agent.application.run_events import ApplicationEventSubscriptions, RunObservation
from naiyue_test_agent.application.session_controls import SessionControlMixin
from naiyue_test_agent.application.session_subagents import SessionSubagentControls
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.registry import ToolRegistry
from naiyue_test_agent.contracts.failures import normalize_classifiers
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy
from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback
from .session_approval import SessionApprovalControls
from .failure_policy import ApplicationErrorPolicy
from .errors import SessionBusyError
from .agent_session import AgentSession


class SessionService(SessionControlMixin, SessionSubagentControls, SessionApprovalControls):
    """Locate sessions and coordinate admission; each session drives its run."""

    def __init__(self, catalog: ModelCatalog, tool_registry: ToolRegistry | None = None, *, initial_active_tools=None, permission_settings=None, progress_settings=None, max_active_runs=10):
        if type(max_active_runs) is not int or max_active_runs <= 0:
            raise ValueError('Root run capacity must be a positive integer')
        self._max_active_runs = max_active_runs
        self._progress_settings = progress_settings
        self._permission_settings = permission_settings if permission_settings is not None else PermissionSettings()
        install_model_usage_callback(catalog)
        self._permission_reviewer = AutoPermissionReviewer.from_catalog(catalog, self._permission_settings.auto)
        self._catalog = catalog
        self._tool_registry = tool_registry if tool_registry is not None else ToolRegistry()
        initial = self._tool_registry.names() if initial_active_tools is None else initial_active_tools
        self._initial_active_tools = tuple(spec.tool.name for spec in self._tool_registry.select(initial))
        self._sessions: dict[str, AgentSession] = {}
        self._subagents: dict[str, dict] = {}
        self._lock = Lock()
        self._events = ApplicationEventSubscriptions()
        self._persistence = None
        self._assembled = False
        self._runtime_classifiers = ()
        self._application_classifiers = ()

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
        self._system_prompt = system_prompt
        self.configure_subagent_runtime(checkpointer, subagent_settings or SubAgentOutputSettings())
        for spec in subagents:
            self.register_subagent(spec)
        return _assemble_interactive_runtime(self._catalog.get(self._catalog.default_model_id),
            tool_registry=self._tool_registry, session_port=self,
            permission_settings=self._permission_settings, permission_reviewer=self._permission_reviewer,
            system_prompt=system_prompt, response_format=response_format,
            checkpointer=checkpointer, observe_turns=True)

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
        return self._session(session_id).resolve_model(agent_path)

    def resolve_prompt_parts(self, session_id):
        return self._session(session_id).resolve_prompt_parts()

    def select_model(self, session_id: str, model_id: str) -> None:
        self._session(session_id).select_model(model_id)

    def _admit(self, session_id, run_id, mode, *, decisions=None, remember_approved=None, stop=False):
        with self._lock:
            session = self._sessions[session_id]
            run_id = run_id or uuid4().hex
            observation = RunObservation(self._events.publisher(session_id, run_id), mode,
                                         progress_settings=self._progress_settings)
            observation.runtime_errors = RuntimeErrorPolicy(self._runtime_classifiers, self._catalog.failure_describer())
            observation.application_errors = ApplicationErrorPolicy(self._application_classifiers)
            capacity_available = (mode == 'approval_resume' and stop or
                sum(actor.is_busy() for actor in self._sessions.values()) < self._max_active_runs)
            run = session.admit_run(run_id, mode, observation, decisions=decisions,
                              remember_approved=remember_approved, stop=stop,
                              capacity_available=capacity_available)
        return session, run

    def pending_tool_approvals(self, session_id: str) -> list[dict[str, str]]:
        return self._session(session_id).pending_tool_approvals()

    async def arun(self, agent, session_id, messages, *, run_id=None, max_concurrency=4):
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError('Tool concurrency must be a positive integer')
        session, run = self._admit(session_id, run_id, 'message')
        return await session._arun(agent, messages, run=run,
                                   max_concurrency=max_concurrency)

    async def acompact(self, agent, session_id, *, run_id=None):
        session, run = self._admit(session_id, run_id, 'compact')
        return await session._arun(agent, [], run=run,
                                   compact=True, system_prompt=self._system_prompt)

    async def aresume_tool_approval(self, agent, session_id, decisions, *, run_id=None, max_concurrency=4, remember_approved=None, stop=False):
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError('Tool concurrency must be a positive integer')
        session, run = self._admit(session_id, run_id, 'approval_resume',
            decisions=decisions, remember_approved=remember_approved, stop=stop)
        return await session.aresume_tool_approval(agent, decisions, run=run, max_concurrency=max_concurrency,
            remember_approved=remember_approved, stop=stop)
