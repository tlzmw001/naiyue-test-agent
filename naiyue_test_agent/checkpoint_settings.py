"""Finite, optional checkpoint maintenance after required session saving."""

from pydantic import BaseModel, ConfigDict, Field


class CheckpointRetentionSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    enabled: bool = True
    trigger_checkpoints: int = Field(default=128, ge=2)
    max_delete_checkpoints: int = Field(default=128, gt=0, le=4096)
    max_delete_bytes: int = Field(default=33554432, gt=0)
