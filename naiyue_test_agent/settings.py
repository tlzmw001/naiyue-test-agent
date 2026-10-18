"""Application configuration aggregates tool budgets without changing the TOML contract."""

import logging
from pathlib import Path
import tomllib

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config_paths import StorageSettings, get_agent_dir
from .tools.permission_rules import PermissionSettings
from .tools.subagent import SubAgentSpec
from .tools.settings import (
    BuiltinToolSettings, ProcessSettings, ToolOutputSettings, ToolProgressSettings,
)


logger = logging.getLogger(__name__)


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


class AppSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    session_title_max_chars: int = Field(default=40, gt=0, le=120)
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


def load_settings(path: str | Path | None = None) -> AppSettings:
    """Permission uncertainty fails startup; only invalid budgets fall back."""
    source = Path(path) if path is not None else get_agent_dir() / 'settings.toml'
    try:
        with source.open('rb') as stream:
            raw = tomllib.load(stream)
    except FileNotFoundError:
        return AppSettings()
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        logger.error('permission_settings_unavailable error_type=%s', type(error).__name__)
        raise ValueError('Permission settings could not be read or parsed') from error
    if set(raw) - {'session_title_max_chars', 'max_active_runs', 'web_event_cache_bytes', 'tools', 'permissions', 'process', 'tool_output', 'storage', 'progress', 'subagents', 'usage_persistence', 'diagnostics'}:
        raise ValueError('Unknown runtime settings section')
    storage = StorageSettings.model_validate(raw.get('storage', {}))
    permissions = PermissionSettings.model_validate(raw.get('permissions', {}))
    max_active_runs = raw.get('max_active_runs', AppSettings.model_fields['max_active_runs'].default)
    if type(max_active_runs) is not int or max_active_runs <= 0:
        logger.warning('max_active_runs_fallback reason=invalid')
        max_active_runs = AppSettings.model_fields['max_active_runs'].default
    title_chars = raw.get('session_title_max_chars', 40)
    if type(title_chars) is not int or not 1 <= title_chars <= 120:
        logger.warning('session_title_budget_fallback reason=invalid')
        title_chars = 40
    web_event_cache_bytes = raw.get('web_event_cache_bytes', AppSettings.model_fields['web_event_cache_bytes'].default)
    if type(web_event_cache_bytes) is not int or web_event_cache_bytes <= 0:
        logger.warning('web_event_cache_bytes_fallback reason=invalid')
        web_event_cache_bytes = AppSettings.model_fields['web_event_cache_bytes'].default
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
        return AppSettings(storage=storage, tools=tools, permissions=permissions, process=process, tool_output=tool_output,
                            progress=progress, usage_persistence=usage_persistence,
                            subagents=raw.get('subagents', ()), max_active_runs=max_active_runs,
                            web_event_cache_bytes=web_event_cache_bytes, diagnostics=diagnostics,
                            session_title_max_chars=title_chars)
    except ValidationError as error:
        logger.error('subagent_settings_invalid error_type=%s', type(error).__name__)
        raise ValueError('Subagent settings are invalid') from None
