"""Bounded soft resource policy; this is not a filesystem hard quota."""
from typing import Literal

from pydantic import Field

from .cloud_tasks import CloudRecord


class ResourceLimits(CloudRecord):
    maintenance_interval_seconds: float = Field(default=30.0, gt=0, le=3600)
    task_batch_size: int = Field(default=100, gt=0, le=1000)
    scan_max_entries: int = Field(default=100000, gt=0, le=1000000)
    scan_max_depth: int = Field(default=64, gt=0, le=128)
    scan_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    scan_cache_seconds: float = Field(default=5.0, ge=0, le=60)
    min_free_bytes: int = Field(default=256 * 1024 * 1024, ge=0)
    max_logical_bytes: int | None = Field(default=None, gt=0)


class DiskUsage(CloudRecord):
    status: Literal['known', 'unknown']
    logical_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    entries: int = Field(ge=0)
    checked_at: float = Field(ge=0)
    reason: str | None = None
