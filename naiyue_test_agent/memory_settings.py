"""Memory queries, lifecycle, bounded background work and prompt preparation."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


REMOVED_MEMORY_FIELDS=frozenset(['injection_tokens', 'personal_convention_tokens', 'comparison_queries', 'comparison_query_chars', 'comparison_query_terms', 'recall_query_limit', 'recall_scan_chars', 'related_limit', 'active_ttl_days', 'candidate_ttl_days', 'preference_limit'])

class MemorySettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    expiry_grace_days: int = Field(default=7,ge=0,le=36500)
    cleanup_batch_size: int = Field(default=100,ge=1,le=1000)
    cleanup_interval_seconds: int = Field(default=3600,ge=0,le=31536000)
    default_ttl_days: int = Field(default=30,ge=1,le=36500)
    wiki_topic_max_memories: int = Field(default=30,ge=1,le=200)
    wiki_navigation_tokens: int = Field(default=2000,ge=0,le=8000)
    session_directory_tokens: int = Field(default=2000,ge=0,le=8000)
    wiki_read_tokens: int = Field(default=2000,ge=256,le=16000)
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
    comparison_material_bytes: int = Field(default=65536, ge=4096, le=262144)
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
    read_chunk_chars: int = Field(default=4096,gt=0)
    batch_max_messages: int = Field(default=128, ge=1, le=1024)
    batch_read_chars: int = Field(default=65536, ge=1024, le=262144)
    batch_overlap_chars: int = Field(default=600, ge=0, le=8192)
    max_output_bytes: int = Field(default=16384, ge=4096)
    source_limit: int = Field(default=3, gt=0, le=8)
    busy_timeout_ms: int = Field(default=2500, ge=0, le=30000)

    @model_validator(mode='after')
    def batch_bounds(self):
        if self.batch_overlap_chars > self.batch_read_chars // 4:
            raise ValueError('batch_overlap_chars exceeds read budget')
        return self

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
