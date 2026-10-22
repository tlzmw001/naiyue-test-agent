"""Prepare and publish one server update on the existing application's loop."""

import asyncio
import logging
import secrets

from ..agent_runtime.async_utils import await_settled
from .owner import ConnectionOwner, MCPResourceError
from .tools import create_mcp_tool


logger = logging.getLogger(__name__)


async def _catalog(resources, name, server, environment, identity):
    owner = ConnectionOwner(name, server, resources._network, environment, identity)
    resources._candidates.add(owner)
    primary = None
    try:
        return (await asyncio.shield(owner.ready)).catalog
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await await_settled(resources._retire(('_refresh', name, identity), owner, primary=primary))
        finally:
            resources._candidates.discard(owner)


async def refresh_mcp(resources, registry, settings, *, borrow_mcp, permission_settings):
    """Failed candidates never replace the effective service or its definitions."""
    async with resources._refresh_lock:
        resources._check_open()
        names = list(dict.fromkeys([*settings.servers, *resources._definitions]))
        resources._last_refresh.clear()
        for name in names:
            resources._check_open()
            server = settings.servers.get(name)
            old = resources._configs.get(name)
            try:
                if server is None or not server.enabled:
                    previous = resources._definitions.get(name)
                    if server is not None:
                        resources._definitions[name] = server.model_copy(deep=True)
                    elif previous is not None:
                        resources._definitions[name] = previous.model_copy(update={'enabled': False})
                    resources._configs.pop(name, None)
                    resources._authorization_ids.pop(name, None)
                    for key, owner in tuple(resources._owners.items()):
                        if owner.server_id == name:
                            owner.retiring = True
                            owner.invalid = True
                            await await_settled(resources._retire(key, owner))
                    continue
                environment, identity = resources._configuration(name, server)
                if old is not None and old[3] != identity:
                    raise MCPResourceError('connection_configuration_changed')
                if old is not None and old[0] == server:
                    continue
                catalog = await _catalog(resources, name, server, environment, identity)
                resources._check_open()
                authority = secrets.token_hex(32)
                specs = [create_mcp_tool(borrow_mcp, server, authority, catalog, tool,
                         resources=resources) for tool in catalog.tools if tool.selected]
                # No await between validation/publication and the effective resource snapshot.
                registry.publish_mcp_tools(specs, permission_settings=permission_settings)
                resources._configs[name] = (server.model_copy(deep=True), resources._network, environment, identity)
                resources._definitions[name] = server.model_copy(deep=True)
                resources._catalogs[name] = catalog
                resources._authorization_ids[name] = authority
                if old is None:
                    resources._slots[name] = asyncio.Semaphore(server.max_concurrency)
            except Exception as error:
                logger.warning('mcp_refresh_failed server=%s error_type=%s', name, type(error).__name__)
                resources._check_open()
                effective = resources._configs.get(name)
                resources._last_refresh[name] = {'server_id': name, 'status': 'error',
                    'enabled': effective is not None, 'selected_tools': list(effective[0].allowed_tools) if effective else [],
                    'error': ('connection_configuration_changed' if isinstance(error, MCPResourceError)
                              and str(error) == 'connection_configuration_changed' else 'mcp_refresh_failed')}
        return resources.server_status()
