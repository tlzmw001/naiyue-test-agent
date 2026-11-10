from naiyue_test_agent.contracts.cloud_networks import NetworkEndpoints
"""Explicit B configuration; no automatic .env/settings discovery."""
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from naiyue_test_agent.contracts.cloud_lifecycle import TemplateReceipt
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.contracts.cloud_runner import RunnerToolEnvironment, task_permissions, validate_runner_configuration
from naiyue_test_agent.tools.subagent import SubAgentSpec, parse_subagent_specs
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactLimits
from naiyue_test_agent.contracts.cloud_models import ModelProxySettings
from naiyue_test_agent.contracts.cloud_history import HistoryLimits
from naiyue_test_agent.contracts.cloud_observation import ObservationLimits
from naiyue_test_agent.contracts.cloud_resources import ResourceLimits
from naiyue_test_agent.contracts.cloud_results import ResultLimits
from naiyue_test_agent.contracts.cloud_mcp import CloudMCPSettings
from naiyue_test_agent.tools.web_settings import NetworkSettings


class BackendSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    endpoint: str
    backend_id: str = Field(min_length=1, max_length=128)
    api_key: SecretStr
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    ready_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    page_size: int = Field(default=100, gt=0, le=1000)
    max_pages: int = Field(default=100, gt=0)

    @model_validator(mode='after')
    def local_endpoint(self):
        url = urlsplit(self.endpoint)
        if (url.scheme not in ('http', 'https') or not ipaddress.ip_address(url.hostname or '').is_loopback
                or url.path not in ('', '/') or url.query or url.fragment or url.username
                or not url.port or not self.api_key.get_secret_value().strip()):
            raise ValueError('invalid_backend_endpoint_or_key')
        return self


class CloudSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    networks: dict[str,NetworkEndpoints] = Field(default_factory=dict,max_length=64)
    data_dir: Path
    protected_roots: tuple[Path, ...] = ()
    owner_listen: str
    task_listen: str
    owner_token: SecretStr
    task_token_ttl_seconds: int = Field(gt=0, strict=True)
    max_frame_bytes: int = Field(default=1024 * 1024, gt=0, strict=True)
    max_receipts: int = Field(default=4096, gt=0, strict=True)
    history_batch_events: int = Field(default=128, gt=0, strict=True)
    json_depth_limit: int = Field(default=32, gt=0, strict=True)
    history_page_limit: int = Field(default=100, gt=0, strict=True)
    history_read_timeout_seconds: float = Field(default=5.0, gt=0, le=300, strict=True)
    history_limits:HistoryLimits=Field(default_factory=HistoryLimits)
    observation_limits:ObservationLimits=Field(default_factory=ObservationLimits)
    backend: BackendSettings | None = None
    templates: dict[str, TemplateReceipt] = Field(default_factory=dict)
    max_active_environments: int = Field(default=2, gt=0, strict=True)
    max_retained_environments: int = Field(default=10, gt=0, strict=True)
    idle_stop_seconds: float = Field(default=1800.0, ge=0)
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    delete_drain_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    task_cpu: int = Field(default=2, gt=0, strict=True)
    task_memory: str = Field(default='2Gi', pattern=r'^[1-9][0-9]*(Mi|Gi)$')
    input_limits: InputLimits = Field(default_factory=InputLimits)
    control_io_timeout_seconds: float = Field(default=15.0,gt=0,le=300)
    control_receive_timeout_seconds: float = Field(default=60.0,gt=0,le=300)
    runner_permissions: PermissionSettings = Field(default_factory=task_permissions)
    tool_environment: RunnerToolEnvironment = Field(default_factory=RunnerToolEnvironment)
    subagents: tuple[SubAgentSpec,...]=()
    _roles=field_validator('subagents',mode='before')(parse_subagent_specs)
    artifact_limits:ArtifactLimits=Field(default_factory=ArtifactLimits)
    result_limits:ResultLimits=Field(default_factory=ResultLimits)
    model_proxy:ModelProxySettings|None=None
    model_network:NetworkSettings=Field(default_factory=NetworkSettings)
    mcp:CloudMCPSettings=Field(default_factory=CloudMCPSettings)

    @field_validator('data_dir')
    @classmethod
    def explicit_root(cls, value):
        if '..' in value.parts:
            raise ValueError('cloud_invalid_root')
        return value.expanduser().absolute()

    @field_validator('owner_token')
    @classmethod
    def require_token(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError('cloud_missing_owner_token')
        return value

    @model_validator(mode='after')
    def listeners(self):
        if self.runner_permissions.mode!='NoAsk':raise ValueError('cloud_requires_noask')
        validate_runner_configuration(self.subagents,self.tool_environment)
        for authority, owner in ((self.owner_listen, True), (self.task_listen, False)):
            url = urlsplit('http://' + authority)
            address = ipaddress.ip_address(url.hostname or '')
            if (not url.port or url.path or url.query or url.fragment or url.username
                    or address.is_unspecified or address.is_multicast
                    or (not address.is_loopback if owner else address.is_loopback or not address.is_private)):
                raise ValueError('cloud_invalid_listener')
        return self

    @model_validator(mode='after')
    def network_ports(self):
        import re
        ports=set()
        for ident,value in self.networks.items():
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}',ident):raise ValueError('invalid_network_id')
            for port in value.ports.values():
                key=(value.bind_host,port)
                if key in ports or f'{value.bind_host}:{port}' in (self.owner_listen,self.task_listen):
                    raise ValueError('duplicate_network_endpoint')
                ports.add(key)
        return self

    @property
    def authority(self):
        return self.owner_listen

    @property
    def access_token(self):
        return self.owner_token.get_secret_value()

    @property
    def origin(self):
        return 'http://' + self.owner_listen
