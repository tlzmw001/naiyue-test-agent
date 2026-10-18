"""Trusted, process-local session state for application entry points."""

from naiyue_test_agent.model_access import load_model_catalog
from naiyue_test_agent.tools.registry import ToolRegistry, ToolSpec
from naiyue_test_agent.tools.settings import load_tool_settings
from naiyue_test_agent.tools.permission_rules import validate_permission_rules


from naiyue_test_agent.application.errors import (
    SessionBusyError, SessionPendingApprovalError, SessionManualReviewRequiredError, RunCapacityExceededError,
)
from naiyue_test_agent.application.session_service import SessionService


def prepare_tool_registry(specs: list[ToolSpec]) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in specs:
        registry.register(spec)
    return registry
