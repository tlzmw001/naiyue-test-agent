"""Trusted, process-local session state for application entry points."""

import logging
from collections import deque
from threading import Lock
from uuid import uuid4
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import AIMessage

from naiyue_test_agent.agent_runtime import (
    _assemble_interactive_runtime, invoke_agent_run, invoke_agent_run_in_workspace,
    resume_agent_run_with_grants,
)
from naiyue_test_agent.agent_runtime.tool_approval import (
    ApprovalReview, approval_policies_for_tools,
)
from naiyue_test_agent.agent_runtime.tools.read import resolve_workspace_root
from naiyue_test_agent.agent_runtime.tools.write import WriteOutcomeUnknown
from naiyue_test_agent.agent_runtime.tools.bash import BashOutcomeUnknown
from naiyue_test_agent.application.project_instructions import load_workspace_instructions
from naiyue_test_agent.application.skills import prepare_run_resources
from naiyue_test_agent.agent_runtime.resource_invocation import (
    invoke_agent_run_with_resources, resume_agent_run_with_resources,
)
from naiyue_test_agent.model_access import ModelCatalog, load_model_catalog


from naiyue_test_agent.application.session_service import (
    SessionBusyError, SessionPendingApprovalError, SessionManualReviewRequiredError,
    SessionService, _pending_tool_approval,
)


logger = logging.getLogger(__name__)


class LocalApplication:
    """Keep one session service paired with its agent and checkpoint."""

    def __init__(self, default_model_id: str, sessions: SessionService, agent):
        self.default_model_id = default_model_id
        self._sessions = sessions
        self._agent = agent

    def subscribe_events(self, handler):
        return self._sessions.subscribe_events(handler)

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

    def resume_tool_approval(self, session_id: str, decisions: list[str]):
        return self._sessions.resume_tool_approval(self._agent, session_id, decisions)


def build_local_application(config_path, *, tools: list, system_prompt: str, response_format):
    """Assemble one process-local application from one model catalog snapshot."""
    catalog = load_model_catalog(config_path)
    sessions = SessionService(catalog, approval_policies_for_tools(tools))
    agent = _assemble_application(catalog, sessions, InMemorySaver(),
        tools=tools, system_prompt=system_prompt, response_format=response_format)
    return LocalApplication(catalog.default_model_id, sessions, agent)


def _assemble_application(catalog, sessions, checkpointer, *, tools, system_prompt, response_format):
    """Shared assembly for actual memory and persistent entry points."""
    return _assemble_interactive_runtime(
        catalog.get(catalog.default_model_id),
        tools=tools,
        system_prompt=system_prompt,
        response_format=response_format,
        resolve_model=sessions.resolve_model,
        record_selection=lambda _session_id, _model_id: None,
        checkpointer=checkpointer,
        drain_steering=sessions.drain_steering,
        finish_pending_messages=sessions.finish_pending_messages,
        is_stop_requested=sessions.is_stop_requested,
        finish_run_boundary=sessions.finish_run_boundary,
        resolve_project_instructions=sessions.resolve_project_instructions,
        observe_turns=True,
        prepare_control_messages=sessions.prepare_control_messages,
    )
