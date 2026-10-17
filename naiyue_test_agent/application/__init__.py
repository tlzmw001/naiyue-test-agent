"""Trusted, process-local session state for application entry points."""

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from naiyue_test_agent.agent_runtime import (
    _assemble_interactive_runtime,
)
from naiyue_test_agent.model_access import load_model_catalog
from naiyue_test_agent.tools.registry import ToolRegistry, ToolSpec
from naiyue_test_agent.tools.settings import load_tool_settings
from naiyue_test_agent.tools.permission_rules import validate_permission_rules


from naiyue_test_agent.application.session_service import (
    SessionBusyError, SessionPendingApprovalError, SessionManualReviewRequiredError,
    SessionService,
)


class LocalApplication:
    """Keep one session service paired with its agent and checkpoint."""

    def __init__(self, default_model_id: str, sessions: SessionService, agent, tool_registry: ToolRegistry):
        self.default_model_id = default_model_id
        self._sessions = sessions
        self._agent = agent
        self._tool_registry = tool_registry

    def get_registered_tools(self) -> tuple[ToolSpec, ...]:
        return self._tool_registry.select(self._tool_registry.names())

    def register_tool(self, spec: ToolSpec) -> None:
        self._sessions.register_tool(spec)

    def get_active_tools(self, session_id: str) -> tuple[str, ...]:
        return self._sessions.get_active_tools(session_id)

    def set_active_tools(self, session_id: str, names) -> None:
        self._sessions.set_active_tools(session_id, names)

    def get_permission_mode(self, session_id):
        return self._sessions.get_permission_mode(session_id)

    def set_permission_mode(self, session_id, mode):
        self._sessions.set_permission_mode(session_id, mode)

    def subscribe_events(self, handler):
        return self._sessions.subscribe_events(handler)

    def configure_error_classifiers(self, *, model_id=None, model=None, runtime=None, application=None):
        return self._sessions.configure_error_classifiers(
            model_id=model_id, model=model, runtime=runtime, application=application)

    def create_session(self, model_id: str) -> str:
        return self._sessions.create_session(model_id)

    def create_workspace_session(self, model_id: str, workspace_root: str | Path) -> str:
        return self._sessions.create_workspace_session(model_id, workspace_root)

    def select_model(self, session_id: str, model_id: str) -> None:
        self._sessions.select_model(session_id, model_id)

    def steer(self, session_id: str, text: str) -> None:
        self._sessions.steer(session_id, text)

    def follow_up(self, session_id: str, text: str) -> None:
        self._sessions.follow_up(session_id, text)

    def request_stop(self, session_id: str, *, run_id: str | None = None) -> list[str]:
        return self._sessions.request_stop(session_id, run_id=run_id)

    def run(self, session_id: str, messages):
        return self._sessions.run(self._agent, session_id, messages)

    def pending_tool_approvals(self, session_id: str) -> list[dict[str, str]]:
        return self._sessions.pending_tool_approvals(session_id)

    def resume_tool_approval(self, session_id: str, decisions: list[str], *, remember_approved=None):
        return self._sessions.resume_tool_approval(self._agent, session_id, decisions, remember_approved=remember_approved)


def prepare_tool_registry(specs: list[ToolSpec]) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in specs:
        registry.register(spec)
    return registry


def build_local_application(config_path, *, tools: list[ToolSpec], system_prompt: str, response_format, initial_active_tools=None, settings=None):
    """Assemble one process-local application from one model catalog snapshot."""
    settings = settings if settings is not None else load_tool_settings()
    registry = prepare_tool_registry(tools)
    validate_permission_rules(settings.permissions, registry.select(registry.names()))
    initial = registry.names() if initial_active_tools is None else initial_active_tools
    registry.select(initial)
    catalog = load_model_catalog(config_path)
    sessions = SessionService(catalog, registry, initial_active_tools=initial, permission_settings=settings.permissions, progress_settings=settings.progress)
    agent = _assemble_application(catalog, sessions, InMemorySaver(),
        tool_registry=registry, system_prompt=system_prompt, response_format=response_format)
    return LocalApplication(catalog.default_model_id, sessions, agent, registry)


def _assemble_application(catalog, sessions, checkpointer, *, tool_registry, system_prompt, response_format):
    """Shared assembly for actual memory and persistent entry points."""
    return _assemble_interactive_runtime(
        catalog.get(catalog.default_model_id),
        tool_registry=tool_registry,
        resolve_active_tool_names=sessions.resolve_active_tool_names,
        permission_settings=sessions._permission_settings, resolve_permission_context=sessions.resolve_permission_context,
        permission_reviewer=sessions._permission_reviewer,
        system_prompt=system_prompt,
        response_format=response_format,
        resolve_model=sessions.resolve_model,
        record_selection=lambda _session_id, _model_id: None,
        checkpointer=checkpointer,
        drain_steering=sessions.drain_steering,
        is_stop_requested=sessions.is_stop_requested,
        finish_run_boundary=sessions.finish_run_boundary,
        resolve_project_instructions=sessions.resolve_project_instructions,
        observe_turns=True,
        prepare_control_messages=sessions.prepare_control_messages,
    )
