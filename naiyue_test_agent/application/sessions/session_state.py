"""Session facts and a separate transient run; storage maps facts explicitly."""

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from _thread import LockType
from naiyue_test_agent.execution_roots import ExecutionRoot
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from naiyue_test_agent.tools.background_tasks import BackgroundNoticeSnapshot
    from naiyue_test_agent.contracts.environment import FileSystemPort
    from naiyue_test_agent.contracts.process import ProcessPort

from naiyue_test_agent.application.observability.run_events import RunObservation
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
    last_execution_path: str | None = None
    execution_binding: Literal['local', 'project_local','sandbox'] | None = None
    execution_setup: dict | None = None

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
    relocation_from: str | None = None
    steering_queue: deque[QueuedInput] = field(default_factory=deque)
    follow_up_queue: deque[QueuedInput] = field(default_factory=deque)
    subagent_instances: dict[str, dict] = field(default_factory=dict)
    subagent_calls: set[str] = field(default_factory=set)
    processes: 'ProcessPort | None' = None
    files: 'FileSystemPort | None' = None
    task_queries: object = None


@dataclass
class SessionRuntime:
    """One live ownership slot shared by the session's explicit collaborators.

    This handle is not persisted. Callers hold lock for paired state transitions
    and for both identity guards; no guard acquires a second lock or performs IO.
    """

    session_id: str
    state: SessionState
    run: ActiveRun | None = None
    lock: LockType = field(default_factory=Lock)

    def require_run(self, run: ActiveRun):
        from naiyue_test_agent.application.errors import SessionBusyError
        if self.run is not run or run is None:
            raise SessionBusyError('State transition targets a different run')

    def require_subagent(self, instance):
        from naiyue_test_agent.tools.permissions import PermissionMechanismError
        if (self.run is None or instance['session_id'] != self.session_id
                or self.run.subagent_instances.get(instance['agent_path']) is not instance):
            raise PermissionMechanismError('Subagent instance belongs to a different session or run')
