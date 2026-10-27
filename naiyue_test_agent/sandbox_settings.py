"""Global Docker execution policy; no daemon is contacted while loading it."""
import re
from pathlib import PurePosixPath
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SandboxNetworkSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    mode: Literal['external', 'none'] = 'external'


class SandboxSSHSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, hide_input_in_errors=True)
    host: str = Field(min_length=1, max_length=255)
    executable: str = 'ssh'
    docker_executable: str = 'docker'
    socket_path: str = '/var/run/docker.sock'
    connect_timeout_seconds: int = Field(default=15, ge=1, le=3600)

    @field_validator('host')
    @classmethod
    def host_alias(cls, value):
        if re.fullmatch(r'\w[\w.-]*', value) is None:
            raise ValueError('SSH target must be an OpenSSH host alias')
        return value

    @field_validator('executable', 'docker_executable')
    @classmethod
    def executable_name(cls, value):
        if not value.strip() or len(value) > 512 or value.startswith('-') or re.search(r'[\x00-\x1f\x7f]', value):
            raise ValueError('Invalid SSH or remote Docker executable')
        return value

    @field_validator('socket_path')
    @classmethod
    def unix_socket(cls, value):
        path = PurePosixPath(value)
        if (not path.is_absolute() or path == PurePosixPath('/') or '..' in path.parts
                or len(value) > 512 or '\\' in value or re.search(r'[\x00-\x1f\x7f]', value)):
            raise ValueError('An absolute remote Unix socket path is required')
        return value


class GitInputSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    executable: str = 'git'
    container_executable: str = 'git'
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_files: int = Field(default=10000, ge=1, le=100000)
    max_file_bytes: int = Field(default=16777216, ge=1, le=268435456)
    max_input_bytes: int = Field(default=67108864, ge=1, le=1073741824)
    max_command_bytes: int = Field(default=8388608, ge=1024, le=67108864)
    max_bundle_bytes: int = Field(default=268435456, ge=1024, le=1073741824)
    max_cache_bytes: int = Field(default=536870912, ge=1024, le=4294967296)
    max_snapshot_records: int = Field(default=128, ge=1, le=10000)

    @field_validator('executable', 'container_executable')
    @classmethod
    def executable_name(cls, value):
        if not value or len(value) > 512 or value.startswith('-') or re.search(r'[\x00-\x1f\x7f]', value):
            raise ValueError('Invalid Git executable')
        return value


class SandboxSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    docker_executable: str = 'docker'
    context: str = 'default'
    ssh: SandboxSSHSettings | None = Field(default=None, exclude_if=lambda value: value is None)
    image: str | None = None
    cpus: float = Field(default=2.0, gt=0, le=64)
    memory_bytes: int = Field(default=2147483648, ge=134217728)
    pids_limit: int = Field(default=256, ge=16, le=65536)
    rpc_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    stop_timeout_seconds: int = Field(default=10, ge=1, le=300)
    max_message_bytes: int = Field(default=16777216, ge=65536, le=67108864)
    process_output_bytes: int = Field(default=8388608, ge=65536, le=1073741824)
    total_output_bytes: int = Field(default=67108864, ge=65536, le=4294967296)
    max_operation_records: int = Field(default=1024, ge=1, le=100000)
    max_active_operations: int = Field(default=16, ge=1, le=256)
    poll_milliseconds: int = Field(default=100, ge=20, le=10000)
    network: SandboxNetworkSettings = Field(default_factory=SandboxNetworkSettings)
    git: GitInputSettings = Field(default_factory=GitInputSettings)

    @model_validator(mode='after')
    def one_connection(self):
        if self.ssh is not None and self.context != 'default':
            raise ValueError('SSH transport cannot be combined with a local Docker context')
        return self

    @field_validator('docker_executable', 'context', 'image')
    @classmethod
    def bounded_name(cls, value, info):
        if value is not None and (not value or len(value) > 512 or value.startswith('-')
                or not value.strip() or re.search(r'[\x00-\x1f\x7f]', value)
                or info.field_name!='docker_executable' and re.search(r'\s',value)):
            raise ValueError('Invalid Docker execution setting')
        return value
