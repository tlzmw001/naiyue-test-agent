"""Run-owned process authority; implementations choose the actual execution site."""
from typing import Protocol, TYPE_CHECKING
from .environment import EnvironmentRef

if TYPE_CHECKING:
    from .run_context import RuntimeContext


class ProcessAccess(Protocol):
    context: 'RuntimeContext'
    tool_call_id: str | None


class ProcessPort(Protocol):
    environment: EnvironmentRef
    owner_session_id: str | None

    def validate(self) -> None: ...
    async def shell(self, name: str, command: str, timeout, background: bool, access: ProcessAccess,
                    *, settings, process_settings, task_settings, progress_settings) -> str: ...
    async def argv(self, args: list[str], timeout, access: ProcessAccess,
                   *, settings, process_settings, progress_settings) -> str: ...
