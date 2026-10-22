"""Session behaviors required by root and child runtime assembly."""

from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from naiyue_test_agent.contracts.replies import QueuedInput
from naiyue_test_agent.tools.subagent import SubAgentSpec


class SessionPort(Protocol):
    """Application-owned behavior; identities come from each invocation context."""

    def resolve_model(self, session_id: str, agent_path: str = 'root') -> tuple[str, BaseChatModel]: ...

    def resolve_request_configuration(self, session_id: str, agent_path: str = 'root') -> tuple[str, BaseChatModel, tuple[str, ...]]: ...

    def resolve_active_tool_names(self, session_id: str, agent_path: str = 'root') -> tuple[str, ...]: ...

    def resolve_permission_context(self, session_id: str, agent_path: str = 'root') -> dict[str, Any]: ...

    # Project resources are shared; transient background updates are root-only.
    def resolve_prompt_parts(self, session_id: str, agent_path: str = 'root') -> list[dict[str, Any]]: ...

    def get_registered_subagents(self) -> tuple[SubAgentSpec, ...]: ...

    # Only the root interactive graph installs hooks that consume these controls.
    def is_stop_requested(self, session_id: str) -> bool: ...

    def drain_steering(self, session_id: str) -> list[QueuedInput]: ...

    def finish_run_boundary(self, session_id: str) -> tuple[bool, list[QueuedInput]]: ...

    def prepare_control_messages(self, session_id: str, messages: list[QueuedInput]) -> list[HumanMessage]: ...
