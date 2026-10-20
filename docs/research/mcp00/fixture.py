"""Real MCP research fixture; synthetic data, never a product default."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'type': 'object', 'additionalProperties': False,
    '$defs': {'selection': {'oneOf': [
        {'type': 'object', 'properties': {'kind': {'const': 'count'}, 'value': {'type': 'integer'}},
         'required': ['kind', 'value'], 'additionalProperties': False},
        {'type': 'object', 'properties': {'kind': {'const': 'label'}, 'value': {'enum': ['A', 'B']}},
         'required': ['kind', 'value'], 'additionalProperties': False}]}},
    'properties': {'selection': {'$ref': '#/$defs/selection'},
                   'note': {'type': 'string', 'default': 'annotation-only'}},
    'required': ['selection'],
}
EMPTY = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deps', required=True)
    parser.add_argument('--audit', required=True)
    parser.add_argument('--port', type=int)
    parser.add_argument('--json-response', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, args.deps)
    from mcp.server.mcpserver import MCPServer
    from mcp.types import Tool, CallToolResult, TextContent, InputRequiredResult
    from mcp.types import ElicitRequest, ElicitRequestFormParams

    path = Path(args.audit)

    def audit(event, **fields):
        with path.open('a') as stream:
            stream.write(json.dumps({'event': event, 'pid': os.getpid(), **fields}) + '\n')

    class Fixture(MCPServer):
        async def list_tools(self):
            return [Tool(name=name, description='Research fixture ' + name,
                         inputSchema=SCHEMA if name == 'echo' else EMPTY)
                    for name in ('echo', 'fail', 'wait', 'lost', 'big', 'ask')]

        async def call_tool(self, name, arguments, context=None):
            audit('call', tool=name, args=arguments)
            if name in ('wait', 'lost'):
                if name == 'lost':
                    audit('committed', tool=name)
                try:
                    await asyncio.Event().wait()
                except BaseException as error:
                    audit('cancel', tool=name, kind=type(error).__name__)
                    raise
            if name == 'ask':
                return InputRequiredResult(inputRequests={'input': ElicitRequest(
                    params=ElicitRequestFormParams(message='research', requestedSchema=EMPTY))})
            if name == 'fail':
                return CallToolResult(content=[TextContent(type='text', text='fixture_failure')], isError=True)
            data = {'pid': os.getpid(), 'args': arguments}
            text = 'x' * 8192 if name == 'big' else json.dumps(data)
            return CallToolResult(content=[TextContent(type='text', text=text)], structuredContent=data)

    audit('start')
    server = Fixture('mcp00')
    if args.port is None:
        server.run()
    else:
        server.run(transport='streamable-http', host='127.0.0.1', port=args.port,
                   json_response=args.json_response)


if __name__ == '__main__':
    main()
