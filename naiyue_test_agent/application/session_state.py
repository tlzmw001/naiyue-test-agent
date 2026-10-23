"""Session facts and a separate transient run; storage maps facts explicitly."""

from collections import deque
from dataclasses import dataclass, field
from naiyue_test_agent.execution_roots import ExecutionRoot
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from naiyue_test_agent.tools.background_tasks import BackgroundNoticeSnapshot

from .run_events import RunObservation
from naiyue_test_agent.contracts.replies import QueuedInput


@dataclass
class SessionState:
    thread_id: str
    model_id: str
    execution_root: ExecutionRoot | None
    active_tool_names: tuple[str, ...]
    permission_mode: str
    project_id: str | None = None
    thinking_level: str = 'model_default'
    conversation_revision: int = 0
    session_allow: list[dict] = field(default_factory=list)
    pending_tool_approval: dict | None = None
    pending_turn: dict | None = None
    manual_review_required: bool = False

    @property
    def execution_path(self):
        return self.execution_root.path if self.execution_root is not None else None


@dataclass(eq=False)
class ActiveRun:
    """One admitted root run, retained through required cleanup and saving."""

    run_id: str
    observation: RunObservation
    project_id: str | None = None
    execution_root: ExecutionRoot | None = None
    reply_id: str | None = None
    prompt_parts: list = field(default_factory=list)
    background_notices: 'BackgroundNoticeSnapshot | None' = None
    project_instructions: str | None = None
    controls_open: bool = True
    steering_open: bool = False
    stop_requested: bool = False
    steering_queue: deque[QueuedInput] = field(default_factory=deque)
    follow_up_queue: deque[QueuedInput] = field(default_factory=deque)
    subagent_instances: dict[str, dict] = field(default_factory=dict)
    subagent_calls: set[str] = field(default_factory=set)
