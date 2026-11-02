"""Owner-confirmed deletion, with durable original physical identity and stage."""
from typing import Literal
from pydantic import Field
from .cloud_tasks import CloudRecord, TaskId, UUIDText, ContentId, TemplateReceipt


class DeleteTask(CloudRecord):
    command_id: UUIDText
    confirm_task_id: TaskId
    expected_revision: int = Field(ge=1)
    expected_generation: int = Field(ge=1)
    preview_digest: ContentId
    acknowledge_unknown: bool = False


class DeleteTombstone(CloudRecord):
    instance_id: UUIDText
    task_id: TaskId
    delete_command_id: UUIDText
    generation: int = Field(ge=1)
    deleted_at: float = Field(ge=0)


class DeleteOperation(CloudRecord):
    command_id: UUIDText
    task_id: TaskId
    instance_id: UUIDText
    generation: int = Field(ge=1)
    template: TemplateReceipt | None
    sandbox_id: str | None
    metadata: dict[str, str]
    environment_command_id: UUIDText | None
    creation_unconfirmed: bool = False
    phase: Literal['reserved', 'stopped', 'cleaning', 'finished'] = 'reserved'
    tombstone: dict | None = None
