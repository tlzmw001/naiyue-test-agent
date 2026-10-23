"""Budgets for the model-visible, read-only archive tool."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HistorySettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    default_limit: int = Field(default=10, gt=0, le=50)
    max_limit: int = Field(default=50, gt=0, le=50)
    search_batch_messages: int = Field(default=200, gt=0, le=10000)
    preview_chars: int = Field(default=300, gt=0)
    read_chunk_chars: int = Field(default=4096, gt=0)
    max_output_bytes: int = Field(default=16384, ge=4096)
    max_query_chars: int = Field(default=2000, gt=0, le=2000)

    @model_validator(mode='after')
    def limits(self):
        if self.default_limit > self.max_limit:
            raise ValueError('History default_limit must not exceed max_limit')
        return self
