"""Durable event transport; payload is task data, never owner authority."""
import hashlib
import json
from typing import Literal

from pydantic import Field, JsonValue

from .cloud_tasks import CloudRecord, TaskId, UUIDText


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


class CloudEvent(CloudRecord):
    seq: int = Field(ge=1)
    source_generation: int = Field(ge=1)
    run_id: TaskId
    kind: Literal['history_record', 'run_observation']
    payload: dict[str, JsonValue]


class EventBatch(CloudRecord):
    task_id: TaskId
    generation: int = Field(ge=1)
    epoch: UUIDText
    events: list[CloudEvent] = Field(min_length=1)
