"""Untrusted browser requests have small, strict and explicit contracts."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class NewSession(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    model_id: str = Field(min_length=1, max_length=128)


class MessageInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
    text: str = Field(min_length=1, max_length=32768)

    @model_validator(mode='after')
    def meaningful_text(self):
        if not self.text.strip():
            raise ValueError('Text must not be blank')
        return self


class ControlInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
    kind: Literal['steering', 'follow_up', 'stop']
    text: str | None = Field(default=None, min_length=1, max_length=32768)

    @model_validator(mode='after')
    def match_control(self):
        if self.kind == 'stop' and self.text is not None:
            raise ValueError('Stop has no message text')
        if self.kind != 'stop' and (self.text is None or not self.text.strip()):
            raise ValueError('A control message is required')
        return self


class ToolDecision(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    tool_call_id: str = Field(min_length=1, max_length=256)
    decision: Literal['approve', 'reject']


class ApprovalInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
    origin_run_id: str = Field(min_length=1, max_length=128)
    decisions: list[ToolDecision] = Field(min_length=1, max_length=128)
