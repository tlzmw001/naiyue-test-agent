"""Bounded, read-only MCP catalog snapshots; no ToolSpec or tools/call."""

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import logging
import re

from ..tools.json_schema import JsonInputContract
from ..tools.web_settings import NetworkSettings
from .settings import TOOL_NAME


logger = logging.getLogger(__name__)


class MCPDiscoveryError(ValueError):
    """Only locally chosen error codes, never remote exception text."""


@dataclass(frozen=True)
class DiscoveredTool:
    remote_name: str
    registered_name: str
    selected: bool
    definition_json: str = field(repr=False)

    @property
    def input_schema(self):
        return json.loads(self.definition_json)['inputSchema']

    @property
    def output_schema(self):
        return json.loads(self.definition_json).get('outputSchema')

    @property
    def description(self):
        return json.loads(self.definition_json).get('description')


@dataclass(frozen=True)
class DiscoveredCatalog:
    server_id: str
    tools: tuple[DiscoveredTool, ...]
    fingerprint: str


async def read_catalog(server_id, settings, session):
    from mcp.types import PaginatedRequestParams
    if not re.fullmatch(r'[A-Za-z0-9_]{1,16}', server_id):
        raise MCPDiscoveryError('invalid_server_id')
    tools, cursors, names = [], set(), set()
    cursor = None
    for _ in range(settings.max_tools + 1):
        page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
        if len(tools) + len(page.tools) > settings.max_tools:
            raise MCPDiscoveryError('tool_limit')
        for remote in page.tools:
            name = remote.name
            registered = f'mcp__{server_id}__{name}'
            if not re.fullmatch(TOOL_NAME, name) or len(registered) > 64:
                raise MCPDiscoveryError('invalid_tool_name')
            if name in names:
                raise MCPDiscoveryError('duplicate_tool')
            names.add(name)
            try:
                schemas = json.dumps([remote.input_schema, remote.output_schema], allow_nan=False,
                                     ensure_ascii=True, separators=(',', ':')).encode()
                if len(schemas) > settings.max_schema_bytes:
                    raise MCPDiscoveryError('schema_limit')
                JsonInputContract(remote.input_schema)
                if remote.output_schema is not None:
                    # Output fields named runtime/config are data, not injected inputs.
                    # Give the nested document its own URI so fragment references
                    # still resolve against that document, not this input wrapper.
                    output = dict(remote.output_schema)
                    if output.get('type') != 'object':
                        raise ValueError('Output schema must declare an object root')
                    output.setdefault('$id', 'urn:naiyue:mcp-output')
                    JsonInputContract({'type': 'object', 'properties': {'result': output}})
                definition = json.dumps(remote.model_dump(by_alias=True, exclude_none=True),
                                        sort_keys=True, separators=(',', ':'), allow_nan=False)
            except MCPDiscoveryError:
                raise
            except (ValueError, TypeError, RecursionError):
                raise MCPDiscoveryError('invalid_schema') from None
            tools.append(DiscoveredTool(name, registered, name in settings.allowed_tools, definition))
        cursor = page.next_cursor
        if cursor is None:
            break
        if not cursor or cursor in cursors:
            raise MCPDiscoveryError('repeated_cursor')
        cursors.add(cursor)
    else:
        raise MCPDiscoveryError('page_limit')
    if set(settings.allowed_tools) - names:
        raise MCPDiscoveryError('missing_allowed_tool')
    definitions = [json.loads(tool.definition_json) for tool in sorted(tools, key=lambda item: item.remote_name)]
    fingerprint = hashlib.sha256(json.dumps(definitions, sort_keys=True, separators=(',', ':'),
                                           allow_nan=False).encode()).hexdigest()
    return DiscoveredCatalog(server_id, tuple(tools), fingerprint)


async def discover_server(server_id, settings, network=None):
    if not settings.enabled:
        raise MCPDiscoveryError('server_disabled')
    from .connections import open_connection
    try:
        # Same task owns deadline, transport, SDK session and teardown.
        async with asyncio.timeout(settings.connect_timeout_seconds):
            async with open_connection(settings, network or NetworkSettings()) as client:
                return await read_catalog(server_id, settings, client.session)
    except MCPDiscoveryError:
        logger.error('mcp_catalog_rejected server=%s', server_id)
        raise
    except Exception as error:
        leaves = []
        def collect(value):
            if isinstance(value, BaseExceptionGroup):
                for member in value.exceptions:
                    collect(member)
            else:
                leaves.append(value)
        collect(error)
        # AnyIO may wrap the same catalog rejection during context teardown.
        # Preserve it only when there is no additional, distinct cleanup failure.
        if leaves and isinstance(leaves[0], MCPDiscoveryError) and all(item is leaves[0] for item in leaves):
            logger.error('mcp_catalog_rejected server=%s', server_id)
            raise leaves[0] from None
        logger.error('mcp_connection_failed error_type=%s', type(error).__name__)
        code = 'discovery_timeout' if isinstance(error, TimeoutError) else 'connection_or_discovery_failed'
        raise MCPDiscoveryError(code) from None
