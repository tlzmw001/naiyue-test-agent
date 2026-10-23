"""Strict proposal/source objects; timestamps and saved states are never inputs."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


Kind = Literal['user', 'rule', 'decision', 'context', 'procedure', 'experience', 'reference']


class MemorySource(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    current_user: bool = False
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, min_length=1, max_length=256)
    message_id: str | None = Field(default=None, min_length=1, max_length=256)
    revision: int | None = Field(default=None, gt=0, le=9223372036854775807)
    conversation_revision: int | None = Field(default=None, ge=0, le=9223372036854775807)
    quote: str = Field(min_length=1, max_length=300)
    offset: int = Field(default=0, ge=0, le=9223372036854775807)

    @model_validator(mode='after')
    def identity(self):
        identity = (self.session_id,self.thread_id,self.message_id,self.revision,self.conversation_revision)
        if (self.current_user and any(v is not None for v in identity)
                or not self.current_user and any(v is None for v in identity)):
            raise ValueError('Use current_user or a complete saved source identity')
        return self


class MemoryProposal(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    kind: Kind
    title: str = Field(min_length=1,max_length=160)
    body: str = Field(min_length=1,max_length=3200)
    subject: str = Field(min_length=1,max_length=128)
    machine: str | None = Field(default=None,min_length=1,max_length=128)
    module: str | None = Field(default=None,min_length=1,max_length=128)
    target: str | None = Field(default=None,min_length=1,max_length=128)
    aliases: list[str] = Field(default_factory=list,max_length=8)
    tags: list[str] = Field(default_factory=list,max_length=8)
    basis: Literal['user_declaration','tool_observation','inference']
    intent: Literal['candidate','activate'] = 'candidate'
    gaps: list[str] = Field(default_factory=list,max_length=8)
    reason: str = Field(min_length=1,max_length=300)
    deadline: float | None = Field(default=None,gt=0,allow_inf_nan=False)

    @model_validator(mode='after')
    def sizes(self):
        if len(self.title.encode())>480 or len(self.body.encode())>3200:
            raise ValueError('Memory UTF8 byte budget exceeded')
        if any(not s.strip() or len(s)>128 for s in [*self.aliases,*self.tags,*self.gaps]):
            raise ValueError('Invalid memory label or gap')
        if not all(v.strip() for v in (self.title,self.body,self.subject,self.reason)):
            raise ValueError('Empty memory content')
        return self
