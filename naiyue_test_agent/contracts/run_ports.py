"""Typed, application-owned capabilities; no service containers or database access."""

from pathlib import Path
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .run_context import RuntimeContext, RunScope
    from ..tools.settings import HistorySettings


class HistoryQuery(Protocol):
    async def __call__(self, *, source_session_id: str, workspace_root: Path | None,
                       agent_path: str, request: dict, settings: 'HistorySettings') -> dict: ...


class MemoryQuery(Protocol):
    async def __call__(self, *, scope: 'RunScope', request: dict,
                       tool_call_id: str | None = None) -> dict: ...


class InvokeSubagent(Protocol):
    async def __call__(self, agent: str, task: str, *, agent_id: str | None,
                       parent_context: 'RuntimeContext', parent_state: dict,
                       parent_tool_call_id: str | None) -> tuple[str, dict]: ...


class ListSubagents(Protocol):
    async def __call__(self, *, parent_context: 'RuntimeContext', parent_state: dict,
                       limit: int | None, before: str | None) -> dict: ...


class TaskQueries(Protocol):
    async def list(self, *, session_id: str | None = None, workspace_root: Path | None = None,
                   limit: int = 20, before: str | None = None) -> dict: ...
    async def output(self, task_id: str, *, session_id: str | None = None,
                     workspace_root: Path | None = None) -> dict: ...
    async def stop(self, task_id: str, *, session_id: str | None = None,
                   workspace_root: Path | None = None) -> dict: ...


class RunControlPort(Protocol):
    @property
    def requested(self) -> bool: ...
    @property
    def reason(self) -> str: ...
    def request(self, reason='user'): ...
    async def await_model(self, call): ...


class RunErrorPolicy(Protocol):
    def describe(self, error, *, source, model_id=None): ...


class ToolBatchPlan(Protocol):
    async def execute(self, call_id, name, handler, limit): ...
