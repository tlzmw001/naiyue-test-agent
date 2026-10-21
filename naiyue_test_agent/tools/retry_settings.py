"""Explicit external-tool retry selection; credentials and remote hints are absent."""

from fnmatch import fnmatchcase
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


MCP_NAME = r'mcp__[A-Za-z0-9_]{1,16}__[A-Za-z0-9_.-]{1,40}'


class ToolRetrySettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, allow_inf_nan=False,
                              hide_input_in_errors=True)

    tools: tuple[str, ...] = ('web_fetch', 'web_search', 'mcp__*')
    max_retries: int = Field(default=2, ge=0)
    attempt_timeout_seconds: float = Field(default=30, gt=0)
    initial_delay_seconds: float = Field(default=1, ge=0)
    backoff_factor: float = Field(default=2, ge=1)
    max_delay_seconds: float = Field(default=10, ge=0)
    replay_safe_tools: tuple[str, ...] = ()

    @field_validator('tools', 'replay_safe_tools', mode='before')
    @classmethod
    def tool_names(cls, value, info):
        if not isinstance(value, (list, tuple)) or any(not isinstance(name, str) for name in value):
            raise ValueError('Retry tools require a list of names')
        for name in value:
            exact = bool(re.fullmatch(MCP_NAME, name)) and len(name) <= 64
            if info.field_name == 'replay_safe_tools':
                valid = exact
            else:
                valid = (exact or name in ('web_fetch', 'web_search', 'mcp__*')
                         or bool(re.fullmatch(r'mcp__[A-Za-z0-9_]{1,16}__\*', name)))
            if not valid:
                raise ValueError('Unsupported retry tool selection')
        if len(set(value)) != len(value):
            raise ValueError('Retry tool names must be unique')
        return tuple(value)

    def applies(self, name):
        return any(fnmatchcase(name, pattern) for pattern in self.tools)

    def can_replay(self, name):
        return self.applies(name) and name in self.replay_safe_tools
