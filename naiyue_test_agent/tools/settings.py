"""Immutable tool budgets loaded once at application startup."""

import codecs
import logging
from pathlib import Path
import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from naiyue_test_agent.config_paths import StorageSettings, get_agent_dir
from .permission_rules import PermissionSettings


logger = logging.getLogger(__name__)


class ReadImageSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    max_input_bytes: int = Field(default=33554432, gt=0)
    max_pixels: int = Field(default=40000000, gt=0)
    max_dimension: int = Field(default=2000, gt=0)
    max_base64_bytes: int = Field(default=4718592, gt=0)


class ReadSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    default_limit: int = Field(default=2000, gt=0)
    max_limit: int = Field(default=2000, gt=0)
    max_output_bytes: int = Field(default=65536, gt=0)
    max_path_chars: int = Field(default=4096, gt=0)
    image: ReadImageSettings = Field(default_factory=ReadImageSettings)

    @model_validator(mode='after')
    def check_limits(self):
        if self.default_limit > self.max_limit:
            raise ValueError('default_limit must not exceed max_limit')
        return self


class LsSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    default_limit: int = Field(default=500, gt=0)
    max_limit: int = Field(default=2000, gt=0)
    max_output_bytes: int = Field(default=51200, gt=0)
    max_path_chars: int = Field(default=4096, gt=0)

    @model_validator(mode='after')
    def check_limits(self):
        if self.default_limit > self.max_limit:
            raise ValueError('default_limit must not exceed max_limit')
        return self


class WriteSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    max_input_bytes: int = Field(default=1048576, gt=0)
    max_path_chars: int = Field(default=4096, gt=0)


class EditSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    max_input_bytes: int = Field(default=1048576, gt=0)
    max_path_chars: int = Field(default=4096, gt=0)
    max_normalization_buffer_bytes: int = Field(default=1048576, gt=0)
    max_affected_line_bytes: int = Field(default=1048576, gt=0)
    max_diff_bytes: int = Field(default=65536, gt=0)
    max_preview_bytes: int = Field(default=65536, gt=0)
    max_patch_bytes: int = Field(default=1048576, gt=0)


class GrepSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    default_limit: int = Field(default=100, gt=0)
    max_limit: int = Field(default=1000, gt=0)
    default_context_lines: int = Field(default=0, ge=0)
    max_context_lines: int = Field(default=10, ge=0)
    max_line_chars: int = Field(default=2000, gt=0)
    max_output_bytes: int = Field(default=51200, gt=0)
    max_pattern_chars: int = Field(default=4096, gt=0)
    max_path_chars: int = Field(default=4096, gt=0)
    max_glob_chars: int = Field(default=4096, gt=0)
    max_record_bytes: int = Field(default=1048576, gt=0)
    max_diagnostic_bytes: int = Field(default=8192, gt=0)
    max_argument_bytes: int = Field(default=16384, gt=0)

    @model_validator(mode='after')
    def check_limits(self):
        if self.default_limit > self.max_limit or self.default_context_lines > self.max_context_lines:
            raise ValueError('Search defaults must not exceed their maximum')
        return self


class FindSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    default_limit: int = Field(default=1000, gt=0)
    max_limit: int = Field(default=2000, gt=0)
    max_output_bytes: int = Field(default=51200, gt=0)
    max_pattern_chars: int = Field(default=4096, gt=0)
    max_path_chars: int = Field(default=4096, gt=0)
    max_record_bytes: int = Field(default=65536, gt=0)
    max_diagnostic_bytes: int = Field(default=8192, gt=0)

    @model_validator(mode='after')
    def check_limits(self):
        if self.default_limit > self.max_limit:
            raise ValueError('default_limit must not exceed max_limit')
        return self


class EnvironmentSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    values: dict[str, str] = Field(default_factory=dict)
    path_prepend: list[str] = Field(default_factory=list)
    unset_env: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_values(self):
        if any(not name or '=' in name or '\0' in name for name in [*self.values, *self.unset_env]):
            raise ValueError('Invalid environment variable name')
        if any('\0' in value for value in [*self.values.values(), *self.path_prepend]):
            raise ValueError('Invalid environment value')
        return self


class ShellSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    executable: str | None = Field(default=None, min_length=1)
    max_command_chars: int = Field(default=8192, gt=0)
    probe_timeout_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    max_output_lines: int = Field(default=2000, gt=0)
    max_output_bytes: int = Field(default=65536, gt=0)
    max_archive_bytes: int = Field(default=16777216, gt=0)
    output_encoding: str = 'utf-8'
    environment: EnvironmentSettings = Field(default_factory=EnvironmentSettings)

    @model_validator(mode='before')
    @classmethod
    def environment_defaults(cls, value):
        if isinstance(value, dict) and isinstance(value.get('environment'), dict):
            environment = value['environment']
            if 'unset_env' not in environment:
                defaults = cls.model_fields['environment'].default_factory()
                return {**value, 'environment': {**environment, 'unset_env': defaults.unset_env}}
        return value

    @model_validator(mode='after')
    def check_limits(self):
        try:
            b''.decode(self.output_encoding, errors='replace')
            codecs.getincrementaldecoder(self.output_encoding)(errors='replace').decode(b'', final=True)
        except (LookupError, TypeError, ValueError) as error:
            raise ValueError('Output encoding must be a supported text codec') from error
        if self.executable is not None and '\0' in self.executable:
            raise ValueError('Invalid executable')
        return self


class BashSettings(ShellSettings):
    environment: EnvironmentSettings = Field(default_factory=lambda: EnvironmentSettings(
        unset_env=['BASH_ENV', 'NAIYUE_WEB_TOKEN']))


class PowerShellSettings(ShellSettings):
    execution_policy: Literal['inherit', 'bypass'] = 'inherit'
    environment: EnvironmentSettings = Field(default_factory=lambda: EnvironmentSettings(
        unset_env=['NAIYUE_WEB_TOKEN']))


class ToolOutputSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    max_workspace_archive_bytes: int = Field(default=134217728, gt=0)


class BuiltinToolSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    read: ReadSettings = Field(default_factory=ReadSettings)
    ls: LsSettings = Field(default_factory=LsSettings)
    write: WriteSettings = Field(default_factory=WriteSettings)
    edit: EditSettings = Field(default_factory=EditSettings)
    grep: GrepSettings = Field(default_factory=GrepSettings)
    find: FindSettings = Field(default_factory=FindSettings)
    bash: BashSettings = Field(default_factory=BashSettings)
    powershell: PowerShellSettings = Field(default_factory=PowerShellSettings)


class ProcessSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    cleanup_timeout_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    shell_exit_quiet_milliseconds: int = Field(default=100, gt=0)
    max_pending_output_bytes: int = Field(default=1048576, gt=0)


class ToolProgressSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    enabled: bool = True
    min_interval_milliseconds: int = Field(default=200, ge=0)
    max_text_bytes: int = Field(default=8192, gt=0)


class ToolSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    storage: StorageSettings = Field(default_factory=StorageSettings)
    tools: BuiltinToolSettings = Field(default_factory=BuiltinToolSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    process: ProcessSettings = Field(default_factory=ProcessSettings)
    tool_output: ToolOutputSettings = Field(default_factory=ToolOutputSettings)
    progress: ToolProgressSettings = Field(default_factory=ToolProgressSettings)


def load_tool_settings(path: str | Path | None = None) -> ToolSettings:
    """Permission uncertainty fails startup; only invalid budgets fall back."""
    source = Path(path) if path is not None else get_agent_dir() / 'settings.toml'
    try:
        with source.open('rb') as stream:
            raw = tomllib.load(stream)
    except FileNotFoundError:
        return ToolSettings()
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        logger.error('permission_settings_unavailable error_type=%s', type(error).__name__)
        raise ValueError('Permission settings could not be read or parsed') from error
    if set(raw) - {'tools', 'permissions', 'process', 'tool_output', 'storage', 'progress'}:
        raise ValueError('Unknown runtime settings section')
    storage = StorageSettings.model_validate(raw.get('storage', {}))
    permissions = PermissionSettings.model_validate(raw.get('permissions', {}))
    try:
        tools = BuiltinToolSettings.model_validate(raw.get('tools', {}))
    except ValidationError:
        logger.warning('tool_settings_fallback reason=invalid')
        tools = BuiltinToolSettings()
    try:
        process = ProcessSettings.model_validate(raw.get('process', {}))
    except ValidationError:
        logger.warning('process_settings_fallback reason=invalid')
        process = ProcessSettings()
    try:
        tool_output = ToolOutputSettings.model_validate(raw.get('tool_output', {}))
    except ValidationError:
        logger.warning('tool_output_settings_fallback reason=invalid')
        tool_output = ToolOutputSettings()
    try:
        progress = ToolProgressSettings.model_validate(raw.get('progress', {}))
    except ValidationError:
        logger.warning('tool_progress_settings_fallback reason=invalid')
        progress = ToolProgressSettings()
    return ToolSettings(storage=storage, tools=tools, permissions=permissions, process=process, tool_output=tool_output,
                        progress=progress)
