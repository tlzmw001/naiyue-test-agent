"""Memory queries, lifecycle, bounded background work and prompt preparation."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemorySettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    enabled: bool = True
    auto_enabled: bool = True
    trigger_turns: int = Field(default=8, gt=0)
    trigger_chars: int = Field(default=20000, gt=0)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    max_input_tokens: int = Field(default=16000, ge=512, le=128000)
    max_output_tokens: int = Field(default=4096, gt=0, le=32000)
    overview_chars: int = Field(default=1200, gt=0, le=1200)
    max_proposals: int = Field(default=8, ge=0, le=8)
    max_batches: int = Field(default=3, gt=0, le=10)
    task_timeout_seconds: int = Field(default=300, gt=0, le=3600)
    max_pending_tasks: int = Field(default=32, gt=0, le=256)
    max_active_tasks: int = Field(default=1, gt=0, le=4)
    injection_tokens: int = Field(default=2000, ge=0, le=8000)
    preference_limit: int = Field(default=3, ge=0, le=10)
    related_limit: int = Field(default=8, ge=0, le=50)
    directory_limit: int = Field(default=8, ge=0, le=50)
    default_limit: int = Field(default=10, gt=0, le=50)
    max_limit: int = Field(default=50, gt=0, le=50)
    candidate_limit: int = Field(default=100, gt=0, le=500)
    field_weights: tuple[float, float, float] = (1.0, 1.0, 1.0)
    fallback_coverage: float = Field(default=0.5, gt=0, le=1)
    rrf_k: int = Field(default=60, gt=0)
    max_query_chars: int = Field(default=2000, gt=0, le=2000)
    max_query_terms: int = Field(default=128, gt=0, le=256)
    preview_chars: int = Field(default=300, gt=0)
    read_chunk_chars: int = Field(default=4096, gt=0)
    max_output_bytes: int = Field(default=16384, ge=4096)
    source_limit: int = Field(default=3, gt=0, le=8)
    busy_timeout_ms: int = Field(default=2500, ge=0, le=30000)

    candidate_ttl_days: dict[Literal['user','rule','decision','context','procedure','experience','reference'], int] = Field(
        default_factory=lambda: dict.fromkeys(('user','rule','decision','context','procedure','experience','reference'),30))
    active_ttl_days: dict[Literal['context','procedure','experience','reference'], int] = Field(
        default_factory=lambda: dict(context=30,procedure=180,experience=365,reference=90))

    @field_validator('candidate_ttl_days','active_ttl_days')
    @classmethod
    def ttl(cls, value, info):
        defaults = (dict.fromkeys(('user','rule','decision','context','procedure','experience','reference'),30)
            if info.field_name == 'candidate_ttl_days' else dict(context=30,procedure=180,experience=365,reference=90))
        if any(type(n) is not int or not 1 <= n <= 36500 for n in value.values()):
            raise ValueError('TTL days must be positive bounded integers')
        return defaults | value

    @field_validator('field_weights', mode='before')
    @classmethod
    def weights(cls, value):
        import math
        if (not isinstance(value, (list, tuple)) or len(value) != 3
                or any(type(n) not in (float, int) or not math.isfinite(n) or n <= 0 for n in value)):
            raise ValueError('Three positive finite weights are required')
        return tuple(float(n) for n in value)

    @model_validator(mode='after')
    def limits(self):
        if self.default_limit > self.max_limit or self.max_limit > self.candidate_limit:
            raise ValueError('Memory result budgets are inconsistent')
        return self
