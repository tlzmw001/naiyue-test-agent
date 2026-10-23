"""Installed command: local Web by default, or the existing terminal chat."""

import argparse
import sys

from naiyue_test_agent import __version__


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='nyt', description='Naiyue Test Agent',
        usage='nyt [Web options] | nyt chat [CLI options] | nyt mcp list | nyt storage {backups,restore}',
        epilog='Default: start local Web. Use nyt chat --help for terminal options. '
               'Web options: --config PATH, --workspace PATH, --host, --port, '
               '--session-db PATH, --new-workspace, --frontend-dist PATH.')
    parser.add_argument('--version', action='version', version=f'nyt {__version__}')
    if args and args[0] in ('--help', '-h', '--version'):
        parser.parse_args(args)
    if args and args[0] == 'chat':
        from .chat import main as chat
        return chat(args[1:])
    if args and args[0] == 'mcp':
        from .mcp import main as mcp
        return mcp(args[1:])
    if args and args[0] == 'storage':
        from .storage import main as storage
        return storage(args[1:])
    if args and not args[0].startswith('-'):
        parser.error(f'unknown command: {args[0]}')
    from naiyue_test_agent.web.__main__ import main as web
    return web(args)
