"""Trusted, process-local session state for application entry points."""

from naiyue_test_agent.agent_runtime import (
    _assemble_interactive_runtime,
)
from naiyue_test_agent.model_access import load_model_catalog
from naiyue_test_agent.tools.registry import ToolRegistry, ToolSpec
from naiyue_test_agent.tools.settings import load_tool_settings
from naiyue_test_agent.tools.permission_rules import validate_permission_rules


from naiyue_test_agent.application.errors import (
    SessionBusyError, SessionPendingApprovalError, SessionManualReviewRequiredError,
)
from naiyue_test_agent.application.session_service import SessionService


def prepare_tool_registry(specs: list[ToolSpec]) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in specs:
        registry.register(spec)
    return registry


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
