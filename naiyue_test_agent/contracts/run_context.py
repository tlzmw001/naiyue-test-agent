"""Explicit lifetimes for runtime identity, preparation and local authorization."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from ..execution_roots import ExecutionRoot
from .run_ports import (
    HistoryQuery, MemoryQuery, InvokeSubagent, ListSubagents, TaskQueries,
    RunControlPort, RunErrorPolicy, ToolBatchPlan,
)
from .run_observation_ports import (
    RunPublisher, TurnObservation, ControlObservation, ContextOperationObservation, ToolProgressObservation,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import SystemMessage
    from ..tools.background_tasks import ProcessTaskPort, BackgroundNoticeSnapshot
    from ..tools.permissions import OneUseApprovalGrants
    from ..tools.permission_rules import PermissionSettings
    from ..tools.registry import ToolSpec


@dataclass(frozen=True)
class RunIdentity:
    session_id: str
    run_id: str | None = None
    agent_path: str = 'root'
    project_id: str | None = None
    execution_root: ExecutionRoot | None = None
    parent_tool_call_id: str | None = None
    parent_run_id: str | None = None
    root_run_id: str | None = None

    @property
    def execution_path(self) -> Path | None:
        return self.execution_root.path if self.execution_root is not None else None


@dataclass(frozen=True)
class RunCapabilities:
    invoke_subagent: InvokeSubagent | None = None
    list_subagents: ListSubagents | None = None
    process_tasks: 'ProcessTaskPort | None' = None
    task_queries: TaskQueries | None = None
    history: HistoryQuery | None = None
    memory: MemoryQuery | None = None


@dataclass(frozen=True)
class RunObservationPorts:
    publisher: 'RunPublisher | None' = None
    turns: 'TurnObservation | None' = None
    controls: 'ControlObservation | None' = None
    context_operations: 'ContextOperationObservation | None' = None
    tool_progress: 'ToolProgressObservation | None' = None

    def __post_init__(self):
        for observer in (self.turns, self.controls, self.context_operations, self.tool_progress):
            if observer is not None and (self.publisher is None or observer.publisher is not self.publisher):
                raise ValueError('Observer publisher does not match its run')


@dataclass(frozen=True)
class RunScope:
    identity: RunIdentity
    capabilities: RunCapabilities = field(default_factory=RunCapabilities)
    observation: RunObservationPorts = field(default_factory=RunObservationPorts)
    control: 'RunControlPort | None' = None
    errors: 'RunErrorPolicy | None' = None
    archive_root: Path | None = None
    background_notices: 'BackgroundNoticeSnapshot | None' = None

    def __post_init__(self):
        notice = self.background_notices
        if notice is not None and (self.identity.agent_path != 'root' or
                (notice.session_id, notice.run_id, notice.workspace_root) !=
                (self.identity.session_id, self.identity.run_id, self.identity.execution_path)):
            raise ValueError('Background notice scope does not match run identity')


@dataclass(frozen=True)
class PreparedRequest:
    model_id: str
    model_call_id: str
    model: 'BaseChatModel'
    active_tool_names: tuple[str, ...] | None = None
    tools: list | None = None
    system_message: 'SystemMessage | None' = None
    background_notices: 'BackgroundNoticeSnapshot | None' = None


@dataclass
class ModelCallState:
    call_id: str | None = None
    model_id: str | None = None
    prepared: PreparedRequest | None = None
    tool_view: 'tuple[ToolSpec, ...] | None' = None
    subagent_roles: tuple[tuple[str, str], ...] = ()


@dataclass
class InvocationState:
    model_call: ModelCallState | None = None
    tool_batch: 'ToolBatchPlan | None' = None
    child_model_failure: tuple[BaseException, str, str] | None = None


class HumanReviewMessage(TypedDict):
    sequence: int
    id: str | None
    content: str


@dataclass(frozen=True)
class InvocationInputs:
    approval_grants: 'OneUseApprovalGrants | None' = None
    read_resource_roots: tuple[Path, ...] = ()
    max_concurrency: int = 4
    human_review_messages: tuple[HumanReviewMessage, ...] | None = None
    subagent_model_id: str | None = None
    subagent_new_delegation: bool = False


@dataclass(frozen=True)
class ToolAuthorization:
    permit: dict | None = None
    permission_rules: 'PermissionSettings | None' = None
    session_allow: tuple[dict, ...] = ()
    permission_mode: str = 'Default'
    review_call_id: str | None = None


@dataclass(frozen=True)
class RuntimeContext:
    scope: RunScope
    invocation: InvocationState = field(default_factory=InvocationState)
    inputs: InvocationInputs = field(default_factory=InvocationInputs)
    authorization: ToolAuthorization | None = None

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        # ToolRuntime's hidden argument is validated by Pydantic as well as tools.
        # It must retain this Python instance, never reconstruct live dependencies.
        from pydantic_core import core_schema
        return core_schema.is_instance_schema(cls, serialization=
            core_schema.plain_serializer_function_ser_schema(lambda value: None))

    @classmethod
    def __get_pydantic_json_schema__(cls, schema, handler):
        return {'title': cls.__name__, 'type': 'object', 'readOnly': True,
                'description': 'Application-owned Python runtime instance; not model input.'}
