"""Official SDK protocol fixture. Synthetic tools are only test data."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import sys

from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import (CallToolResult, DiscoverResult, InputRequiredResult, ListToolsResult,
                       RequestParams, TextContent, ImageContent, Tool)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--mode', default='normal')
    parser.add_argument('--http', action='store_true')
    parser.add_argument('--json-response', action='store_true')
    parser.add_argument('--legacy', action='store_true')
    args = parser.parse_args()

    def audit(event, **values):
        with args.audit.open('a') as stream:
            stream.write(json.dumps({'event': event, **values}) + '\n')

    audit('start', pid=os.getpid(), inherited_key='OPENAI_API_KEY' in os.environ,
          explicit=os.environ.get('FIXTURE_VALUE'))
    sys.stderr.write('PRIVATE-STDERR-' * 2000 + '\n')
    sys.stderr.flush()

    async def listing(ctx, params):
        cursor = params.cursor if params else None
        audit('list', cursor=cursor)
        if args.mode == 'wait' or args.audit.with_suffix('.block-catalog').exists():
            await asyncio.Event().wait()
        if args.mode == 'empty':
            return ListToolsResult(tools=[])
        schema = {'type': 'object', 'properties': {'text': {'type': 'string'}}}
        if args.mode == 'invalid':
            schema['properties']['runtime'] = {}
        if args.mode == 'large':
            schema['description'] = 'x' * 20000
        description = 'changed' if args.audit.with_suffix('.drift').exists() else None
        remote = Tool(name='echo' if cursor is None else 'other', inputSchema=schema, description=description)
        return ListToolsResult(tools=[remote], nextCursor=('next' if cursor is None else None))

    count = 0
    async def calling(ctx, params):
        nonlocal count
        if args.mode in ('stateful', 'tools'):
            count += 1
            audit('call', count=count, pid=os.getpid())
            action = (params.arguments or {}).get('text')
            if action == 'drop':
                os._exit(3)
            if action == 'wait':
                while not args.audit.with_suffix('.release').exists():
                    await asyncio.sleep(.02)
            if args.mode == 'tools':
                if action == 'interaction':
                    return InputRequiredResult(requestState='PRIVATE-STATE')
                if action == 'media':
                    return CallToolResult(content=[ImageContent(type='image', data='PRIVATE-MEDIA', mimeType='image/png')])
                if action == 'large':
                    return CallToolResult(content=[TextContent(type='text', text='大' * 5000)])
                if action == 'error':
                    return CallToolResult(content=[TextContent(type='text', text='Fixture execution error')], isError=True)
            value = {'count': count, 'pid': os.getpid()}
            return CallToolResult(content=[TextContent(type='text', text=json.dumps(value))], structuredContent=value)
        audit('call')
        raise AssertionError('tools/call must never be used by discovery')

    server = Server('discovery-fixture', on_list_tools=listing, on_call_tool=calling)
    if args.mode == 'legacy' or args.legacy:
        async def legacy_discover(ctx, params):
            return DiscoverResult(supported_versions=['2025-11-25'], capabilities=server.get_capabilities())
        server.add_request_handler('server/discover', RequestParams, legacy_discover)

    async def stdio():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    async def http():
        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Mount

        manager = StreamableHTTPSessionManager(server, json_response=args.json_response)

        @asynccontextmanager
        async def lifespan(app):
            async with manager.run():
                yield

        async def endpoint(scope, receive, send):
            if args.mode == 'auth':
                audit('auth_rejected')
                await PlainTextResponse('PRIVATE-AUTH-BODY', status_code=401)(scope, receive, send)
                return
            if scope['method'] == 'DELETE':
                audit('delete')
            await manager.handle_request(scope, receive, send)

        app = Starlette(routes=[Mount('/mcp', app=endpoint)], lifespan=lifespan)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            sock.listen()
            audit('ready', port=sock.getsockname()[1])
            await uvicorn.Server(uvicorn.Config(app, log_level='error')).serve(sockets=[sock])

    asyncio.run(http() if args.http else stdio())


if __name__ == '__main__':
    main()
