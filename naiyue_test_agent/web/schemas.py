"""Untrusted browser requests have small, strict and explicit contracts."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPRefreshInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class NewSession(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    model_id: str = Field(min_length=1, max_length=128)


class SessionPresentation(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None

    @model_validator(mode='after')
    def meaningful_change(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('A presentation change is required')
        if self.title is not None:
            if not self.title.strip() or any(ord(c) < 32 or ord(c) == 127 for c in self.title):
                raise ValueError('A text title is required')
            self.title = self.title.strip()
        return self


class SessionOrder(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    session_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(max_length=10000)

    @model_validator(mode='after')
    def unique_ids(self):
        if len(set(self.session_ids)) != len(self.session_ids):
            raise ValueError('Duplicate session ID')
        return self


class ThinkingInput(NewSession):
    thinking_level: Literal['model_default', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']


class CompactInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


class RegenerateInput(CompactInput):
    expected_reply_id: str = Field(min_length=1, max_length=128)
    expected_conversation_revision: int = Field(ge=0)


class ActiveToolsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    active_tools: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(max_length=256)


class PermissionModeInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    permission_mode: Literal['Default', 'NoAsk', 'Auto', 'BypassApprovals']


class RecoveryInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    checkpoint_id: str = Field(min_length=1, max_length=128)
    action: Literal['continue', 'restore_approval']


class MessageInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
    text: str = Field(min_length=1, max_length=32768)
    expected_conversation_revision: int | None = Field(default=None, ge=0)

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
    agent_path: str = Field(default='root', min_length=1, max_length=256)

    @model_validator(mode='after')
    def match_control(self):
        if self.agent_path != 'root' and self.kind != 'stop':
            raise ValueError('Only stop may target a child agent')
        if self.kind == 'stop' and self.text is not None:
            raise ValueError('Stop has no message text')
        if self.kind != 'stop' and (self.text is None or not self.text.strip()):
            raise ValueError('A control message is required')
        return self


class ToolDecision(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    approval_id: str = Field(min_length=1, max_length=256)
    decision: Literal['approve', 'reject']


class ApprovalInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
    origin_run_id: str = Field(min_length=1, max_length=128)
    approval_batch_id: str = Field(min_length=1, max_length=128)
    decisions: list[ToolDecision] = Field(min_length=1, max_length=128)
    remember_approved: list[str] = Field(default_factory=list, max_length=128)


class ApprovalStopInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
    origin_run_id: str = Field(min_length=1, max_length=128)
    approval_batch_id: str = Field(min_length=1, max_length=128)
