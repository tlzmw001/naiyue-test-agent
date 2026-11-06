"""Cloud MCP service configuration and bounded Runner projections."""
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .cloud_networks import EntryId, ServiceTarget
from .cloud_tasks import CloudRecord, UUIDText
from ..mcp_client.settings import MCPServerSettings, MCPSettings, TOOL_NAME


MCP_HEADERS_CAPABILITY = 'mcp:headers'


class CloudMCPServer(CloudRecord):
    url: str = Field(min_length=1, max_length=2048, repr=False)
    enabled: bool = True
    route: Literal['direct', 'windows_tunnel'] = 'direct'
    network_slot: EntryId | None = None
    allow_insecure_http: bool = False
    headers_env: dict[str, str] = Field(default_factory=dict, repr=False, exclude_if=lambda value: not value)
    connect_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    call_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    idle_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)

    def client_settings(self):
        return MCPServerSettings(transport='streamable_http', **self.model_dump(exclude={'route', 'network_slot'}))

    @model_validator(mode='after')
    def check_endpoint(self):
        self.client_settings()
        if '?' in self.url or urlsplit(self.url).port == 0 or (self.route == 'windows_tunnel') != (self.network_slot is not None):
            raise ValueError('invalid_cloud_mcp_endpoint')
        return self


class CloudMCPSettings(CloudRecord):
    servers: dict[str, CloudMCPServer] = Field(default_factory=dict, max_length=8)

    def credential_names(self):
        return {name for server in self.servers.values() if server.enabled for name in server.headers_env.values()}

    @model_validator(mode='after')
    def check_ids(self):
        MCPSettings(servers={name: server.client_settings() for name, server in self.servers.items()})
        return self


class CloudMCPSection(CloudRecord):
    """Read just the MCP section of the Coordinator's trusted settings file."""
    model_config = CloudRecord.model_config | {'extra': 'ignore'}
    mcp: CloudMCPSettings = Field(default_factory=CloudMCPSettings)


class RefreshMCP(CloudRecord):
    refresh_id: UUIDText


class CloudMCPServiceStatus(CloudRecord):
    server_id: str = Field(pattern=r'^[A-Za-z0-9_]{1,16}$')
    route: Literal['direct', 'windows_tunnel']
    tools: list[str] = Field(max_length=64)
    status: Literal['available', 'disabled', 'error']
    error: Literal['mcp_tunnel_target_unavailable', 'mcp_refresh_failed'] | None = None

    @model_validator(mode='after')
    def check_tool_names(self):
        prefix = f'mcp__{self.server_id}__'
        if len(set(self.tools)) != len(self.tools) or any(
                not name.startswith(prefix) or re.fullmatch(TOOL_NAME, name[len(prefix):]) is None
                for name in self.tools):
            raise ValueError('invalid_cloud_mcp_tools')
        return self

def tunnel_target(server, services):
    """Return only the authorized TCP dial target for the original MCP origin."""
    slot = (services or {}).get('slots', {}).get(server.network_slot)
    if slot is None:
        return None
    endpoint = urlsplit(server.url)
    target = ServiceTarget(address=slot['address'])
    tls = endpoint.scheme == 'https'
    if (endpoint.hostname, endpoint.port or (443 if tls else 80), tls) != (target.host, target.port, target.tls):
        return None
    return slot['connect_host'], slot['connect_port']
