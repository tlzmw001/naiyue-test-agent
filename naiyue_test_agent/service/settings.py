"""Explicit service identity; configuration paths are compared without credentials."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from naiyue_test_agent import __version__
from naiyue_test_agent.config_paths import get_agent_dir, get_models_path


class ServiceError(RuntimeError):
    def __init__(self, code, *, reason=None, startup=None):
        self.code, self.reason, self.startup = code, reason, startup
        super().__init__(code)


class StartupFailure(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    database_path: str = Field(min_length=1, max_length=32768)
    phase: str = Field(pattern=r'^[a-zA-Z0-9_]{1,80}$')
    target_schema: int = Field(ge=0)
    source_schema: int | None = Field(default=None, ge=0)
    current_schema: int | None = Field(default=None, ge=0)
    backup_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


def user_identity():
    if os.name != 'nt':
        return str(os.getuid())
    import ctypes
    from ctypes import wintypes
    size = wintypes.DWORD(257)
    name = ctypes.create_unicode_buffer(size.value)
    if not ctypes.windll.advapi32.GetUserNameW(name, ctypes.byref(size)):
        raise OSError('service_user_unavailable')
    return name.value.casefold()


def canonical(path):
    return Path(os.path.normcase(str(Path(path).expanduser().resolve())))


class ServiceIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    product_version: str = Field(min_length=1, max_length=128)
    protocol_version: int = Field(ge=1)
    user_id: str = Field(min_length=1, max_length=256)
    data_dir: str = Field(min_length=1, max_length=32768)
    config_paths: dict[str, str]

    def check(self, other):
        for field, code in (('product_version', 'service_version_mismatch'),
                ('protocol_version', 'service_protocol_mismatch'), ('user_id', 'service_user_mismatch'),
                ('data_dir', 'service_data_dir_mismatch'), ('config_paths', 'service_config_mismatch')):
            if getattr(self, field) != getattr(other, field):
                raise ServiceError(code)


class ServiceOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    data_dir: Path
    config_path: Path = Field(default_factory=get_models_path)
    settings_path: Path = Field(default_factory=lambda: get_agent_dir() / 'settings.toml')
    frontend_dist: Path
    host: str = '127.0.0.1'
    port: int = Field(default=0, ge=0, le=65535)
    startup_timeout: float = Field(default=30, gt=0, le=300)
    request_timeout: float = Field(default=10, gt=0, le=300)
    session_poll_interval: float = Field(default=0.25, gt=0, le=30)
    max_read_reconnects: int = Field(default=3, ge=0, le=10)
    max_receipts: int = Field(default=4096, gt=0)
    receipt_cache_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    max_frame_bytes: int = Field(default=1024 * 1024, ge=1024)

    @field_validator('data_dir', 'config_path', 'settings_path', 'frontend_dist')
    @classmethod
    def normalized(cls, value):
        return canonical(value)

    @field_validator('host')
    @classmethod
    def local_only(cls, value):
        if value not in ('127.0.0.1', '::1'):
            raise ValueError('Service requires a loopback address')
        return value

    @property
    def identity(self):
        return ServiceIdentity(product_version=__version__, protocol_version=1,
            user_id=user_identity(), data_dir=str(self.data_dir),
            config_paths={'models': str(self.config_path), 'settings': str(self.settings_path)})

    @property
    def origin(self):
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f'http://{host}:{self.port}'


class ServiceRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    instance_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    identity: ServiceIdentity
    origin: str
    access_token: str = Field(min_length=32, max_length=256, repr=False)

    @field_validator('origin')
    @classmethod
    def local_origin(cls, value):
        url = urlsplit(value)
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', '::1')
                or not url.port or url.username is not None or url.password is not None
                or url.path or url.query or url.fragment):
            raise ValueError('Invalid local service origin')
        host = f'[{url.hostname}]' if ':' in url.hostname else url.hostname
        if value != f'http://{host}:{url.port}':
            raise ValueError('Invalid local service origin')
        return value
