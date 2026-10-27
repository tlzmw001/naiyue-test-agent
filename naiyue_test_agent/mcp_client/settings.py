"""Trusted MCP configuration, independent of SDK and model imports."""

import ipaddress
import re
import logging
from pathlib import Path
import tomllib
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


ENV_NAME = r'[A-Za-z_][A-Za-z0-9_]*'
TOOL_NAME = r'[A-Za-z0-9_.-]{1,40}'


class MCPServerSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, hide_input_in_errors=True,
                              allow_inf_nan=False)

    enabled: bool = False
    transport: Literal['stdio', 'streamable_http']
    command: str | None = Field(default=None, repr=False)
    args: list[str] = Field(default_factory=list, repr=False)
    cwd: str | None = Field(default=None, repr=False)
    env: dict[str, str] = Field(default_factory=dict, repr=False)
    url: str | None = Field(default=None, repr=False)
    headers_env: dict[str, str] = Field(default_factory=dict, repr=False)
    allow_insecure_http: bool = False
    allow_sandbox: bool = False
    allowed_tools: list[str] = Field(default_factory=list)
    connect_timeout_seconds: float = Field(default=15, gt=0)
    call_timeout_seconds: float = Field(default=0, ge=0)
    read_timeout_seconds: float = Field(default=0, ge=0)
    idle_timeout_seconds: float = Field(default=0, ge=0)
    max_tools: int = Field(default=64, gt=0)
    max_schema_bytes: int = Field(default=65536, gt=0)
    max_response_bytes: int = Field(default=1048576, gt=0)
    max_result_bytes: int = Field(default=65536, gt=0)
    max_concurrency: int = Field(default=1, gt=0)

    @model_validator(mode='after')
    def check_transport(self):
        if len(set(self.allowed_tools)) != len(self.allowed_tools) or any(
                not re.fullmatch(TOOL_NAME, name) for name in self.allowed_tools):
            raise ValueError('Invalid MCP allowed tools')
        if self.transport == 'stdio':
            if not self.command or self.url is not None or self.headers_env or self.allow_insecure_http:
                raise ValueError('Invalid stdio fields')
            if any('\0' in value for value in [self.command, *self.args, self.cwd or '']):
                raise ValueError('Invalid subprocess argument')
            for key, value in self.env.items():
                if not re.fullmatch(ENV_NAME, key) or not re.fullmatch(r'\$\{' + ENV_NAME + r'\}', value):
                    raise ValueError('stdio environment requires explicit ${ENV_NAME} references')
        else:
            if self.command is not None or self.args or self.cwd is not None or self.env or not self.url:
                raise ValueError('Invalid HTTP fields')
            try:
                url = urlsplit(self.url)
                port = url.port
                if (url.scheme not in ('https', 'http') or not url.hostname or url.username is not None
                        or url.password is not None or url.fragment or '#' in self.url
                        or any(ord(c) < 33 or ord(c) == 127 for c in self.url)):
                    raise ValueError('Invalid URL')
            except ValueError:
                raise ValueError('MCP requires a valid HTTP(S) URL without userinfo or fragment') from None
            try:
                loopback = ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:
                loopback = url.hostname.lower() == 'localhost'
            if url.scheme == 'http' and (self.headers_env or not (loopback or self.allow_insecure_http)):
                raise ValueError('Plain HTTP requires loopback or opt-in and forbids credential headers')
            denied = {'host', 'connection', 'content-length', 'content-type', 'accept', 'accept-encoding',
                      'transfer-encoding', 'mcp-session-id', 'mcp-protocol-version'}
            if len({key.lower() for key in self.headers_env}) != len(self.headers_env):
                raise ValueError('Duplicate MCP headers')
            for header, name in self.headers_env.items():
                if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header)
                        or header.lower() in denied or not re.fullmatch(ENV_NAME, name)):
                    raise ValueError('Invalid MCP credential header reference')
        return self


class MCPSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, hide_input_in_errors=True)
    servers: dict[str, MCPServerSettings] = Field(default_factory=dict)

    @model_validator(mode='after')
    def check_names(self):
        if any(not re.fullmatch(r'[A-Za-z0-9_]{1,16}', name) for name in self.servers):
            raise ValueError('Invalid MCP server identity')
        return self


def load_mcp_settings(path=None):
    """Explicit runtime refresh reads only the MCP section, never writes settings."""
    from ..config_paths import get_agent_dir
    source = Path(path) if path is not None else get_agent_dir() / 'settings.toml'
    try:
        with source.open('rb') as stream:
            raw = tomllib.load(stream)
        return MCPSettings.model_validate(raw.get('mcp', {}))
    except (OSError, UnicodeError, ValueError) as error:
        logging.getLogger(__name__).warning('mcp_refresh_settings_invalid error_type=%s', type(error).__name__)
        raise ValueError('MCP settings could not be refreshed') from None
