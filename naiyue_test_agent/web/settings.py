"""Explicit local-server settings; credentials never appear in object repr."""

from pathlib import Path
import secrets

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WebSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    config_path: Path
    database_path: Path
    frontend_dist: Path
    workspace_root: Path | None = None
    host_bash: bool = False
    host: str = '127.0.0.1'
    port: int = Field(default=0, ge=0, le=65535)
    access_token: str = Field(default_factory=lambda: secrets.token_urlsafe(32), min_length=8, repr=False)
    event_capacity: int = Field(default=256, gt=0)
    max_frame_bytes: int = Field(default=1024 * 1024, gt=0)
    retained_runs: int = Field(default=16, gt=0)
    max_receipts: int = Field(default=4096, gt=0)

    @field_validator('host')
    @classmethod
    def loopback_only(cls, value):
        if value not in ('127.0.0.1', '::1'):
            raise ValueError('Local Web requires a loopback address')
        return value

    @field_validator('workspace_root')
    @classmethod
    def canonical_workspace(cls, value):
        return value.expanduser().resolve(strict=True) if value is not None else None

    @model_validator(mode='after')
    def bash_workspace(self):
        if self.host_bash and self.workspace_root is None:
            raise ValueError('Bash requires a workspace')
        return self

    @property
    def authority(self):
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f'{host}:{self.port}'

    @property
    def origin(self):
        return f'http://{self.authority}'
