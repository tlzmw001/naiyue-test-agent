"""Prepare and publish one server update on the existing application's loop."""

import asyncio
import logging
import secrets
import time

from ..agent_runtime.async_utils import await_settled
from .owner import ConnectionOwner, MCPResourceError
from .tools import create_mcp_tool


logger = logging.getLogger(__name__)


async def _catalog(resources, name, server, environment, identity, dial_target, dial_deadline=None):
    if server.transport == 'streamable_http':
        discovery_deadline = time.time() + server.connect_timeout_seconds
        dial_deadline = min(discovery_deadline, dial_deadline) if dial_deadline is not None else discovery_deadline
    owner = ConnectionOwner(name, server, resources._network, environment, identity,
        dial_target=dial_target, dial_deadline=dial_deadline, select_all=resources.select_all)
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


async def _cloud_catalogs(resources, settings, dial_targets, dial_deadline):
    configs = {name: resources._configuration(name, server, dial_targets.get(name))
               for name, server in settings.servers.items() if server.enabled}
    values = await asyncio.gather(*(
        _catalog(resources, name, settings.servers[name], environment, identity, dial_targets.get(name), dial_deadline)
        for name, (environment, identity) in configs.items()
    ), return_exceptions=True)
    return dict(zip(configs, values)), configs


async def refresh_mcp(resources, registry, settings, *, borrow_mcp, permission_settings, dial_targets=None, dial_deadline=None):
    """Failed candidates never replace the effective service or its definitions."""
    previous_request = resources._last_refresh_request
    async with resources._refresh_lock:
        resources._check_open()
        completed = resources._last_refresh_request
        if dial_targets is None and completed is not previous_request and completed == settings:
            return resources.server_status()
        names = list(dict.fromkeys([*settings.servers, *resources._definitions]))
        resources._last_refresh.clear()
        catalogs, configs = await _cloud_catalogs(resources, settings, dial_targets, dial_deadline) if dial_targets is not None else ({}, {})
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
                    await resources.disable_server(name)
                    continue
                dial_target = (dial_targets or {}).get(name)
                environment, identity = configs[name] if name in configs else resources._configuration(name, server, dial_target)
                if old is not None and old[3] != identity:
                    if not resources.select_all:
                        raise MCPResourceError('connection_configuration_changed')
                    await resources.disable_server(name)
                    old = None
                if name in catalogs:
                    catalog = catalogs[name]
                    if isinstance(catalog, BaseException):
                        raise catalog
                else:
                    catalog = await _catalog(resources, name, server, environment, identity, dial_target)
                resources._check_open()
                effective_catalog = resources._catalogs.get(name)
                if (old is not None and old[0] == server and effective_catalog is not None
                        and effective_catalog.fingerprint == catalog.fingerprint):
                    continue
                authority = secrets.token_hex(32)
                specs = [create_mcp_tool(borrow_mcp, server, authority, catalog, tool,
                         resources=resources) for tool in catalog.tools if tool.selected]
                # No await between validation/publication and the effective resource snapshot.
                registry.publish_mcp_tools(specs, permission_settings=permission_settings)
                resources._configs[name] = (server.model_copy(deep=True), resources._network, environment, identity)
                resources._definitions[name] = server.model_copy(deep=True)
                resources._dial_targets[name] = dial_target
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
        resources._last_refresh_request = settings.model_copy(deep=True)
        return resources.server_status()
