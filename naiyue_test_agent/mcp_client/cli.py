"""Read-only discovery: safe JSON output, no Agent or tool execution."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from ..settings import load_settings


logger = logging.getLogger(__name__)


async def _list(settings):
    rows = []
    failed = False
    for name, server in settings.mcp.servers.items():
        if not server.enabled:
            continue
        from .discovery import discover_server, MCPDiscoveryError
        try:
            catalog = await discover_server(name, server, settings.network)
            rows.append({'server': name, 'status': 'ok', 'fingerprint': catalog.fingerprint,
                         'tools': [{'original_name': item.remote_name,
                                    'registered_name': item.registered_name,
                                    'selected': item.selected, 'schema': 'valid'} for item in catalog.tools]})
        except Exception as error:
            # Third-party exception bodies may contain credentials, payloads and URLs.
            logger.error('mcp_discovery_failed server=%s', name)
            code = str(error) if isinstance(error, MCPDiscoveryError) else 'mcp_discovery_failed'
            rows.append({'server': name, 'status': 'error', 'error': code})
            failed = True
    print(json.dumps({'servers': rows}, ensure_ascii=True))
    return int(failed)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='nyt mcp', description='Read-only MCP discovery')
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='Discover configured tools without calling them')
    listing.add_argument('--settings', help='Explicit settings.toml; defaults to the user settings')
    args = parser.parse_args(argv)
    try:
        if args.settings is not None and not Path(args.settings).is_file():
            raise ValueError('Explicit MCP settings file does not exist')
        settings = load_settings(args.settings)
    except (ValueError, OSError):
        logger.error('mcp_settings_invalid')
        print(json.dumps({'error': 'mcp_settings_invalid'}))
        return 1
    try:
        return asyncio.run(_list(settings))
    except KeyboardInterrupt:
        logger.warning('mcp_discovery_cancelled')
        return 130
