"""Application configuration aggregates tool budgets without changing the TOML contract."""

import logging
from pathlib import Path
import re
import tomllib

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config_paths import StorageSettings, get_agent_dir
from .project_settings import ProjectsSettings
from .image_input_settings import ImageInputSettings
from .sandbox_settings import SandboxSettings
from .sandbox_profiles import SandboxConfiguration
from .delivery_settings import DeliverySettings
from .checkpoint_settings import CheckpointRetentionSettings
from .shell_task_settings import ShellTaskSettings
from .memory_settings import MemorySettings
from .mcp_client.settings import MCPSettings
from .tools.web_settings import NetworkSettings
from .tools.retry_settings import ToolRetrySettings
from .tools.permission_rules import PermissionSettings
from .tools.subagent import SubAgentSpec
from .tools.settings import (
    BuiltinToolSettings, ProcessSettings, ToolProgressSettings,
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
    default_active_tools: tuple[str, ...] | None = None
    storage: StorageSettings = Field(default_factory=StorageSettings)
    image_inputs: ImageInputSettings = Field(default_factory=ImageInputSettings)
    projects: ProjectsSettings = Field(default_factory=ProjectsSettings)
    sandbox: SandboxConfiguration = Field(default_factory=SandboxConfiguration)
    delivery: DeliverySettings = Field(default_factory=DeliverySettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    tool_retry: ToolRetrySettings = Field(default_factory=ToolRetrySettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    tools: BuiltinToolSettings = Field(default_factory=BuiltinToolSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    process: ProcessSettings = Field(default_factory=ProcessSettings)
    shell_tasks: ShellTaskSettings = Field(default_factory=ShellTaskSettings)
    progress: ToolProgressSettings = Field(default_factory=ToolProgressSettings)
    usage_persistence: UsagePersistenceSettings = Field(default_factory=UsagePersistenceSettings)
    diagnostics: DiagnosticSettings = Field(default_factory=DiagnosticSettings)
    checkpoint_retention: CheckpointRetentionSettings = Field(default_factory=CheckpointRetentionSettings)
    subagents: tuple[SubAgentSpec, ...] = ()

    @field_validator('sandbox',mode='before')
    @classmethod
    def sandbox_configuration(cls,value):
        if isinstance(value,SandboxSettings) and not isinstance(value,SandboxConfiguration):
            return value.model_dump(mode='python')
        return value

    @field_validator('default_active_tools', mode='before')
    @classmethod
    def active_tool_names(cls, value):
        if value is None:
            return None
        if (not isinstance(value, (list, tuple))
                or any(not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', name) for name in value)
                or len(set(value)) != len(value)):
            raise ValueError('Default active tools require unique tool names')
        return tuple(value)

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
    """Permission and execution-policy uncertainty fail startup."""
    source = Path(path) if path is not None else get_agent_dir() / 'settings.toml'
    try:
        with source.open('rb') as stream:
            raw = tomllib.load(stream)
    except FileNotFoundError:
        return AppSettings()
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        logger.error('permission_settings_unavailable error_type=%s', type(error).__name__)
        raise ValueError('Permission settings could not be read or parsed') from error
    if set(raw) - {'session_title_max_chars', 'max_active_runs', 'web_event_cache_bytes', 'default_active_tools', 'tools', 'permissions', 'process', 'tool_output', 'shell_tasks', 'storage', 'projects', 'sandbox', 'delivery', 'progress', 'subagents', 'usage_persistence', 'diagnostics', 'checkpoint_retention', 'network', 'mcp', 'tool_retry', 'memory', 'image_inputs'}:
        raise ValueError('Unknown runtime settings section')
    try:
        memory = MemorySettings.model_validate(raw.get('memory', {}))
    except ValidationError:
        logger.warning('memory_settings_invalid action=disabled')
        memory = MemorySettings(enabled=False)
    if 'tool_output' in raw:
        legacy_output = raw['tool_output']
        if not isinstance(legacy_output, dict) or set(legacy_output) - {'max_workspace_archive_bytes'}:
            raise ValueError('Unknown tool output settings')
        logger.warning('tool_output_settings_removed key=max_workspace_archive_bytes action=ignored')
    try:
        shell_tasks = ShellTaskSettings.model_validate(raw.get('shell_tasks', {}))
    except ValidationError:
        logger.error('shell_task_settings_invalid')
        raise ValueError('Shell task settings are invalid') from None
    try:
        default_active_tools = AppSettings.active_tool_names(raw.get('default_active_tools'))
    except ValueError:
        logger.error('default_active_tools_invalid')
        raise ValueError('Default active tools are invalid') from None
    try:
        mcp = MCPSettings.model_validate(raw.get('mcp', {}))
    except ValidationError:
        logger.error('mcp_settings_invalid')
        raise ValueError('MCP settings are invalid') from None
    try:
        network = NetworkSettings.model_validate(raw.get('network', {}))
    except ValidationError:
        logger.error('network_settings_invalid')
        raise ValueError('Network settings are invalid') from None
    try:
        tool_retry = ToolRetrySettings.model_validate(raw.get('tool_retry', {}))
    except ValidationError:
        logger.error('tool_retry_settings_invalid')
        raise ValueError('Tool retry settings are invalid') from None
    image_inputs = ImageInputSettings.model_validate(raw.get('image_inputs', {}))
    storage = StorageSettings.model_validate(raw.get('storage', {}))
    projects = ProjectsSettings.model_validate(raw.get('projects', {}))
    sandbox = SandboxConfiguration.model_validate(raw.get('sandbox', {}))
    delivery = DeliverySettings.model_validate(raw.get('delivery', {}))
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
        checkpoint_retention = CheckpointRetentionSettings.model_validate(raw.get('checkpoint_retention', {}))
    except ValidationError:
        logger.warning('checkpoint_retention_invalid action=disabled')
        checkpoint_retention = CheckpointRetentionSettings(enabled=False)
    try:
        return AppSettings(image_inputs=image_inputs, storage=storage, projects=projects, sandbox=sandbox, delivery=delivery, memory=memory, tools=tools, network=network, mcp=mcp, tool_retry=tool_retry, permissions=permissions, process=process, shell_tasks=shell_tasks,
                            default_active_tools=default_active_tools,
                            progress=progress, usage_persistence=usage_persistence,
                            subagents=raw.get('subagents', ()), max_active_runs=max_active_runs,
                            web_event_cache_bytes=web_event_cache_bytes, diagnostics=diagnostics,
                            checkpoint_retention=checkpoint_retention,
                            session_title_max_chars=title_chars)
    except ValidationError as error:
        logger.error('subagent_settings_invalid error_type=%s', type(error).__name__)
        raise ValueError('Subagent settings are invalid') from None
