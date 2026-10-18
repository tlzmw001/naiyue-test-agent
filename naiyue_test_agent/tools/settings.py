"""Immutable tool budgets loaded once at application startup."""

import codecs
import logging
from pathlib import Path
import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from naiyue_test_agent.config_paths import StorageSettings, get_agent_dir
from .permission_rules import PermissionSettings
from .subagent import SubAgentSpec


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


class UsagePersistenceSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    max_pending_events: int = Field(default=1024, gt=0)
    max_pending_bytes: int = Field(default=4194304, gt=0)


class DiagnosticSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    max_pending_events: int = Field(default=1024, gt=0)
    max_pending_bytes: int = Field(default=4194304, gt=0)
    max_run_bytes: int = Field(default=1048576, gt=0)
    summary_reserve_bytes: int = Field(default=4096, ge=2048)
    max_workspace_bytes: int = Field(default=268435456, gt=0)
    retention_days: int = Field(default=30, gt=0)
    cleanup_batch_runs: int = Field(default=32, gt=0, le=1024)

    @model_validator(mode='after')
    def check_budgets(self):
        if not self.summary_reserve_bytes < self.max_run_bytes <= self.max_workspace_bytes:
            raise ValueError('Diagnostic run and workspace budgets are inconsistent')
        return self


class ToolSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    max_active_runs: int = Field(default=10, gt=0)
    web_event_cache_bytes: int = Field(default=4194304, gt=0)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    tools: BuiltinToolSettings = Field(default_factory=BuiltinToolSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    process: ProcessSettings = Field(default_factory=ProcessSettings)
    tool_output: ToolOutputSettings = Field(default_factory=ToolOutputSettings)
    progress: ToolProgressSettings = Field(default_factory=ToolProgressSettings)
    usage_persistence: UsagePersistenceSettings = Field(default_factory=UsagePersistenceSettings)
    diagnostics: DiagnosticSettings = Field(default_factory=DiagnosticSettings)
    subagents: tuple[SubAgentSpec, ...] = ()

    @field_validator('subagents', mode='before')
    @classmethod
    def role_definitions(cls, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError('Subagent settings require a list of definitions')
        specs = []
        for item in value:
            if isinstance(item, dict):
                fields = dict(item)
                if isinstance(fields.get('tool_names'), list):
                    fields['tool_names'] = tuple(fields['tool_names'])
                try:
                    item = SubAgentSpec(**fields)
                except TypeError as error:
                    raise ValueError('Invalid subagent definition fields') from error
            if not isinstance(item, SubAgentSpec):
                raise ValueError('Subagent settings require definitions')
            specs.append(item)
        if len({spec.name for spec in specs}) != len(specs):
            raise ValueError('Duplicate subagent names in settings')
        return tuple(specs)


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
    if set(raw) - {'max_active_runs', 'web_event_cache_bytes', 'tools', 'permissions', 'process', 'tool_output', 'storage', 'progress', 'subagents', 'usage_persistence', 'diagnostics'}:
        raise ValueError('Unknown runtime settings section')
    storage = StorageSettings.model_validate(raw.get('storage', {}))
    permissions = PermissionSettings.model_validate(raw.get('permissions', {}))
    max_active_runs = raw.get('max_active_runs', ToolSettings.model_fields['max_active_runs'].default)
    if type(max_active_runs) is not int or max_active_runs <= 0:
        logger.warning('max_active_runs_fallback reason=invalid')
        max_active_runs = ToolSettings.model_fields['max_active_runs'].default
    web_event_cache_bytes = raw.get('web_event_cache_bytes', ToolSettings.model_fields['web_event_cache_bytes'].default)
    if type(web_event_cache_bytes) is not int or web_event_cache_bytes <= 0:
        logger.warning('web_event_cache_bytes_fallback reason=invalid')
        web_event_cache_bytes = ToolSettings.model_fields['web_event_cache_bytes'].default
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
    try:
        usage_persistence = UsagePersistenceSettings.model_validate(raw.get('usage_persistence', {}))
    except ValidationError:
        logger.warning('usage_persistence_settings_fallback reason=invalid')
        usage_persistence = UsagePersistenceSettings()
    try:
        diagnostics = DiagnosticSettings.model_validate(raw.get('diagnostics', {}))
    except ValidationError:
        logger.warning('diagnostic_settings_fallback reason=invalid')
        diagnostics = DiagnosticSettings()
    try:
        return ToolSettings(storage=storage, tools=tools, permissions=permissions, process=process, tool_output=tool_output,
                            progress=progress, usage_persistence=usage_persistence,
                            subagents=raw.get('subagents', ()), max_active_runs=max_active_runs,
                            web_event_cache_bytes=web_event_cache_bytes, diagnostics=diagnostics)
    except ValidationError as error:
        logger.error('subagent_settings_invalid error_type=%s', type(error).__name__)
        raise ValueError('Subagent settings are invalid') from None
