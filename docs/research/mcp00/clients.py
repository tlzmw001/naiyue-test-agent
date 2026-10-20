"""Equal-condition research candidates. No production registration or config IO."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys

LAB = Path(__file__).resolve().parent
PROJECT = LAB.parents[2]
DEPS = os.environ.get('MCP00_DEPS_DIR') or Path('/tmp/nyt-mcp-compare-deps-path.txt').read_text().strip()
sys.path.insert(0, DEPS)
sys.path.insert(1, str(PROJECT))
from mcp.client.client import Client as SDKClient
from mcp.client.stdio import StdioServerParameters
from mcp.types import InputRequiredResult
from fastmcp import Client as FastClient
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from fastmcp.client.elicitation import ElicitResult
from langchain.mcp import MCPAdapter
from langchain_core.tools import ToolException

logger = logging.getLogger(__name__)


def read_audit(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def until(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(.03)


async def decline(*args, **kwargs):
    return ElicitResult(action='decline')


class Candidate:
    def __init__(self, route, audit, url=None, *, wire_budget=False):
        self.route, self.audit = route, audit
        self.budget_facts = []
        from http_budget import http_factory, sdk_transport
        audit.write_text('')
        arguments = [str(LAB / 'fixture.py'), '--deps', DEPS, '--audit', str(audit)]
        if route == 'sdk':
            target = sdk_transport(url, self.budget_facts) if url and wire_budget else url
            self.client = SDKClient(target or StdioServerParameters(command=sys.executable, args=arguments))
            self.context = self.client
        else:
            transport = (StreamableHttpTransport(url, httpx_client_factory=http_factory(self.budget_facts))
                         if url and wire_budget else url)
            transport = transport or StdioTransport(sys.executable, arguments, keep_alive=False,
                                             log_file=audit.with_suffix('.stderr'))
            self.client = FastClient(transport, elicitation_handler=decline, input_required_max_rounds=0)
            self.context = MCPAdapter(self.client)
        self.enter_task = self.exit_task = None

    async def __aenter__(self):
        self.enter_task = asyncio.current_task()
        await self.context.__aenter__()
        if self.route == 'sdk':
            self.schemas = {t.name: t.input_schema for t in (await self.client.list_tools()).tools}
        else:
            self.tools = {t.name: t.model_copy(update={'handle_tool_error': False})
                          for t in await self.context.list_tools()}
            self.schemas = {name: t.args_schema for name, t in self.tools.items()}
        return self

    async def __aexit__(self, *exc):
        self.exit_task = asyncio.current_task()
        try:
            return await self.context.__aexit__(*exc)
        finally:
            if self.route == 'lc':
                await self.client.close()

    async def call(self, name, arguments):
        try:
            return await self._call(name, arguments)
        except Exception as error:
            if self.budget_facts:
                # A fatal read can surface as generic "Connection closed" on
                # the RPC waiter. Preserve the actual owner-level read error.
                original = self.budget_facts[-1]['error']
                if original is not error:
                    raise original from error
            raise

    async def _call(self, name, arguments):
        if self.route == 'sdk':
            result = await self.client.session.call_tool(name, arguments, allow_input_required=True)
            if isinstance(result, InputRequiredResult):
                raise RuntimeError('unsupported_input_required')
            if result.is_error:
                raise ToolException('fixture_failure')
            if any(b.type != 'text' for b in result.content):
                raise ValueError('unsupported_content')
            value = {'content': [b.text for b in result.content], 'structured': result.structured_content}
        else:
            result = await self.tools[name].ainvoke(
                {'type': 'tool_call', 'id': 'inner', 'name': name, 'args': arguments})
            blocks = result.content if isinstance(result.content, list) else [result.content]
            if any(isinstance(b, dict) and b.get('type') != 'text' for b in blocks):
                raise ValueError('unsupported_content')
            value = {'content': [b['text'] if isinstance(b, dict) else b for b in blocks],
                     'structured': (result.artifact or {}).get('structured_content')}
        # Same post-parse policy, deliberately NOT a transport/memory hard limit.
        if len(json.dumps(value).encode()) > 4096:
            raise ValueError('result_budget_exceeded')
        return value


class OwnedClient:
    """One task enters/exits; callers borrow. Research model for MCP-03, no TTL."""
    def __init__(self, candidate):
        self.candidate = candidate
        self.ready = asyncio.get_running_loop().create_future()
        self.stop = asyncio.Event()
        self.active = set()
        self.closing = False
        self.task = None
        self.observed_errors = []
        self.cleanup_errors = []

    async def start(self):
        self.task = asyncio.create_task(self._own(), name='mcp00-owner')
        await asyncio.shield(self.ready)
        return self

    async def _own(self):
        try:
            async with self.candidate:
                self.ready.set_result(None)
                await self.stop.wait()
        except BaseException as error:
            if not self.ready.done():
                self.ready.set_exception(error)
            raise

    async def call(self, name, args):
        if self.closing:
            raise RuntimeError('owner_closing')
        caller = asyncio.current_task()
        self.active.add(caller)
        try:
            return await self.candidate.call(name, args)
        except Exception as error:
            self.observed_errors.append(error)
            raise
        finally:
            self.active.discard(caller)

    async def close(self):
        self.closing = True
        pending = tuple(self.active)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.stop.set()
        try:
            await self.task
        except Exception as error:
            def leaves(value):
                if isinstance(value, BaseExceptionGroup):
                    return [leaf for member in value.exceptions for leaf in leaves(member)]
                return [value]
            if not all(any(leaf is known for known in self.observed_errors) for leaf in leaves(error)):
                raise
            # Only a repeat of the EXACT original failure is diagnostic. Any
            # additional cleanup failure still propagates. No text matching.
            if not any(error is known for known in self.cleanup_errors):
                self.cleanup_errors.append(error)
                logger.warning('mcp00_cleanup_repeated_failure error_type=%s', type(error).__name__)


@asynccontextmanager
async def owned(route, path, url=None):
    owner = OwnedClient(Candidate(route, path, url))
    try:
        await owner.start()
        yield owner
    finally:
        if owner.task is not None:
            await owner.close()


@asynccontextmanager
async def http_fixture(path, *, json_response=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    path.write_text('')
    with path.with_suffix('.stderr').open('w') as log:
        command = [sys.executable, str(LAB / 'fixture.py'), '--deps', DEPS,
                   '--audit', str(path), '--port', str(port)]
        if json_response:
            command.append('--json-response')
        proc = subprocess.Popen(command, stdout=log, stderr=log)
        try:
            async with asyncio.timeout(15):
                while True:
                    assert proc.poll() is None
                    try:
                        reader, writer = await asyncio.open_connection('127.0.0.1', port)
                    except OSError:
                        await asyncio.sleep(.03)
                    else:
                        writer.close()
                        await writer.wait_closed()
                        break
            yield f'http://127.0.0.1:{port}/mcp'
        finally:
            proc.terminate()
            await asyncio.to_thread(proc.wait, 15)
