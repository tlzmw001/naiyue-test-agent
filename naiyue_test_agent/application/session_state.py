"""Session facts and a separate transient run; storage maps facts explicitly."""

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .run_events import RunObservation
from naiyue_test_agent.contracts.replies import QueuedInput


@dataclass
class SessionState:
    thread_id: str
    model_id: str
    workspace_root: Path | None
    active_tool_names: tuple[str, ...]
    permission_mode: str
    thinking_level: str = 'model_default'
    conversation_revision: int = 0
    session_allow: list[dict] = field(default_factory=list)
    pending_tool_approval: dict | None = None
    pending_turn: dict | None = None
    manual_review_required: bool = False


@dataclass(eq=False)
class ActiveRun:
    """One admitted root run, retained through required cleanup and saving."""

    run_id: str
    observation: RunObservation
    reply_id: str | None = None
    prompt_parts: list = field(default_factory=list)
    project_instructions: str | None = None
    controls_open: bool = True
    steering_open: bool = False
    stop_requested: bool = False
    steering_queue: deque[QueuedInput] = field(default_factory=deque)
    follow_up_queue: deque[QueuedInput] = field(default_factory=deque)
    subagent_instances: dict[str, dict] = field(default_factory=dict)
