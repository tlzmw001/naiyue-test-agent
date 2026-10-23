"""One scoped bridge for run dependencies; never stores application objects."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING
from collections.abc import Callable
from pathlib import Path
from naiyue_test_agent.execution_roots import ExecutionRoot

if TYPE_CHECKING:
    from .events import RunEventPublisher, AgentEventPublisher
    from .errors import RuntimeErrorPolicy
    from .run_control import RunControl
    from .turn_observation import TurnObserver
    from .control_observation import ControlObserver
    from .context_operation_observation import ContextOperationObserver
    from .tool_execution.observation import ToolProgressObserver
    from ..tools.background_tasks import ProcessTaskPort, BackgroundNoticeSnapshot


@dataclass(frozen=True)
class RunServices:
    publisher: 'RunEventPublisher | AgentEventPublisher | None' = None
    errors: 'RuntimeErrorPolicy | None' = None
    control: 'RunControl | None' = None
    turns: 'TurnObserver | None' = None
    controls: 'ControlObserver | None' = None
    context_operations: 'ContextOperationObserver | None' = None
    tool_progress: 'ToolProgressObserver | None' = None
    ainvoke_subagent: Callable | None = None
    process_tasks: 'ProcessTaskPort | None' = None
    background_notices: 'BackgroundNoticeSnapshot | None' = None
    alist_subagents: Callable | None = None
    history_query: Callable | None = None
    memory_query: Callable | None = None
    task_queries: object | None = None
    archive_root: Path | None = None
    project_id: str | None = None
    execution_root: ExecutionRoot | None = None


_RUN_SERVICES = ContextVar('naiyue_run_services', default=None)


@contextmanager
def bind_run_services(services):
    token = _RUN_SERVICES.set(services)
    try:
        yield
    finally:
        _RUN_SERVICES.reset(token)


def current_run_services():
    return _RUN_SERVICES.get()
