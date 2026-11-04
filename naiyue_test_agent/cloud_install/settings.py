"""Explicit private operator configuration; never supplied by task messages."""
from pathlib import Path,PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field,field_validator,SecretStr
from naiyue_test_agent.contracts.cloud_tasks import CloudRecord
from .template import TemplateOperatorSettings


class UpdateSettings(CloudRecord):
    work_dir: Path
    index: str = Field(min_length=1,max_length=4096)
    trusted_prefixes: list[str] = Field(min_length=1,max_length=16)
    platform: Literal['linux-arm64','linux-x86_64']
    build: TemplateOperatorSettings
    connection_file: Path
    task_data_dir: Path|None = None
    cloud_settings_file: Path|None = None
    recovery_dir: Path|None = None
    maintenance_receipt: Path|None = None
    channel: str = Field(default='stable',pattern=r'^[a-z][a-z0-9_-]{0,63}$')
    auto_activate: bool = True
    max_cache_bytes: int = Field(default=2*1024**3,gt=0)
    min_free_bytes: int = Field(default=256*1024**2,ge=0)
    keep_backups: int = Field(default=3,ge=1,le=1000)
    gc_max_resources: int = Field(default=256,ge=1,le=4096)
    gc_backup_verify_bytes: int = Field(default=8*1024**3,gt=0)
    max_artifact_bytes: int = Field(default=256*1024*1024,gt=0,le=2**34)
    max_index_bytes: int = Field(default=1024*1024,gt=0,le=16*1024*1024)
    download_proxy: SecretStr|None = None
    request_timeout_seconds: float = Field(default=60.0,gt=0,le=3600)
    io_bytes: int = Field(default=256*1024,gt=0,le=1024*1024)
    backup_max_bytes: int = Field(default=4*1024**3,gt=0)
    backup_memory: str = Field(default='512m',pattern=r'^[1-9][0-9]{0,5}[kmg]$')
    backup_cpus: float = Field(default=1.0,gt=0,le=8)
    backup_pids: int = Field(default=64,gt=0,le=512)
    backup_max_files: int = Field(default=100000,gt=0)

    @field_validator('download_proxy')
    @classmethod
    def proxy(cls,value):
        if value is not None:
            parsed=urlsplit(value.get_secret_value())
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.path not in ('','/') or parsed.query or parsed.fragment:
                raise ValueError('invalid_download_proxy')
        return value

    @field_validator('work_dir','connection_file','task_data_dir','cloud_settings_file','recovery_dir','maintenance_receipt')
    @classmethod
    def absolute(cls,value):
        if value is not None and (not value.is_absolute() or '..' in value.parts):raise ValueError('absolute_update_path_required')
        return value

    @field_validator('trusted_prefixes')
    @classmethod
    def prefixes(cls,values):
        for value in values:
            p=urlsplit(value)
            if p.scheme!='https' or not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment or not value.endswith('/'):
                raise ValueError('invalid_trusted_release_prefix')
        return values


class RemoteUpdateSettings(CloudRecord):
    ssh_host: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    ssh_executable: str = Field(default='ssh',min_length=1,max_length=4096)
    remote_python: str = Field(min_length=1,max_length=4096)
    remote_settings: str = Field(min_length=1,max_length=4096)
    timeout_seconds: float = Field(default=60.0,gt=0,le=3600)

    @field_validator('remote_python','remote_settings')
    @classmethod
    def absolute_remote(cls,value):
        if (not PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts
                or any(ord(c)<32 or ord(c)==127 for c in value)):
            raise ValueError('invalid_remote_update_path')
        return value
