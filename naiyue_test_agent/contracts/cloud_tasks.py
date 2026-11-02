"""B identities and control facts, independent of A execution bindings."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

UUIDText = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
TaskId = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{32}$')]
ContentId = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
Label = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Lifecycle = Literal['creating', 'stopped', 'starting', 'ready', 'stopping', 'reconciling', 'deleting', 'deleted', 'failed']


class CloudError(Exception):
    def __init__(self, code, status=409, *, last_seq=None):
        super().__init__(code)
        self.code, self.status, self.last_seq = code, status, last_seq


class CloudRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False, revalidate_instances='always')


class CreateProject(CloudRecord):
    command_id: UUIDText
    name: Label

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('blank_name')
        return value


class CloudProject(CloudRecord):
    project_id: UUIDText
    name: Label
    revision: int = Field(ge=1)
    created_at: float = Field(ge=0)


class TemplateReceipt(CloudRecord):
    template_id: Label
    runner_release_id: Label
    image_id: str = Field(pattern=r'^sha256:[0-9a-f]{64}$')
    base_image: str = Field(pattern=r'^[a-z0-9][a-z0-9/_.:-]*@sha256:[0-9a-f]{64}$')
    wheel_sha256: ContentId
    kit_version: Label
    kit_wheel_sha256: ContentId
    uid: int = Field(gt=0)
    gid: int = Field(gt=0)
    backend_id: Label
    toolchain_sha256: ContentId
    preflight_sha256: ContentId

    @property
    def fingerprint(self):
        # Delayed import keeps the task/event DTO modules independently loadable.
        from .cloud_events import digest
        return digest(self.model_dump(exclude={'preflight_sha256'}))


class CloudTask(CloudRecord):
    task_id: TaskId
    project_id: UUIDText
    title: str = Field(max_length=1024)
    input_id: ContentId
    template_id: Label
    runner_release_id: Label
    state_schema: int = Field(ge=1)
    sandbox_id: Label | None = None
    generation: int = Field(ge=1)
    revision: int = Field(ge=1)
    lifecycle: Lifecycle
    active_run_id: TaskId | None = None
    created_at: float = Field(ge=0)
    updated_at: float = Field(ge=0)
    last_error_code: Label | None = None
    result_ids: list[UUIDText] = Field(default_factory=list, max_length=1024)
    last_quiescent_at: float | None = Field(default=None, ge=0)
    reclaim_blockers: list[Label] = Field(default_factory=list, max_length=128)
    environment_phase: Literal['absent', 'preparing', 'backend_ready', 'unknown'] = 'absent'
    environment_operation_id: UUIDText | None = None
    template_digest: ContentId | None = None
    template_snapshot: TemplateReceipt | None = None
    input_ready: bool = False
    runner_phase: Literal['unconfigured','booting','ready','waiting_model','blocked','disconnected'] = 'unconfigured'
    runner_boot_id: UUIDText | None = None
    runner_connection_id: UUIDText | None = None
    state_epoch: UUIDText | None = None
    runner_model_ids: list[Label] = Field(default_factory=list,max_length=128)
    runner_capabilities:list[Label]=Field(default_factory=list,max_length=128)
    artifact_operation_id:UUIDText|None=None
    control_operation_id:UUIDText|None=None
    last_run_id:TaskId|None=None
    runner_recovery:dict|None=None
    model_profile_id:ContentId|None=None
    delete_command_id:UUIDText|None=None


class CommandReceipt(CloudRecord):
    command_id: UUIDText
    resource_id: UUIDText | TaskId
    operation: Label
    expected_generation: int | None = Field(default=None, ge=1)
    request_digest: ContentId
    status: Literal['accepted', 'running', 'succeeded', 'failed', 'unknown']
    http_status: int = Field(ge=100, le=599)
    result: dict = Field(default_factory=dict)
    error_code: Label | None = None
    created_at: float = Field(ge=0)
    updated_at: float = Field(ge=0)


class StoredTaskCredential(CloudRecord):
    credential_id: UUIDText
    token_hash: ContentId
    task_id: TaskId
    project_id: UUIDText
    generation: int = Field(ge=1)
    scopes: list[Literal['identity:read', 'events:append', 'control:connect','artifacts:write','models:invoke']]
    expires_at: float = Field(gt=0)
    revoked_at: float | None = None
