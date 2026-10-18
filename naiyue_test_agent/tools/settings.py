"""Immutable tool budgets loaded once at application startup."""

import codecs
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

    max_diff_input_bytes: int = Field(default=1048576, gt=0)
    max_diff_bytes: int = Field(default=65536, gt=0)
    max_preview_bytes: int = Field(default=65536, gt=0)
    max_patch_bytes: int = Field(default=1048576, gt=0)

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


class SubAgentOutputSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    max_result_lines: int = Field(default=2000, gt=0)
    max_result_bytes: int = Field(default=65536, gt=0)


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
    subagent: SubAgentOutputSettings = Field(default_factory=SubAgentOutputSettings)


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
