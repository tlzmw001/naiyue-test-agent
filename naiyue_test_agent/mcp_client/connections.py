"""Temporary SDK connections: same-task ownership and controlled transports."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import logging
import os
import threading
from urllib.parse import urlsplit

import httpx2

from ..tools.web_network import selected_proxy
from .settings import MCPServerSettings


logger = logging.getLogger(__name__)
_in_connection = ContextVar('mcp_connection_logging', default=False)


class _SafeLibraryLogs(logging.Filter):
    def filter(self, record):
        if _in_connection.get():
            record.msg = 'mcp_library_diagnostic source=%s level=%s'
            record.args = (record.name, record.levelname)
            record.exc_info = record.exc_text = record.stack_info = None
        return True


@contextmanager
def _safe_library_logs():
    # SDK tasks inherit this context. Other users of HTTPX in this process do not.
    names = set(logging.Logger.manager.loggerDict)
    names.update(('client', 'httpx2', 'httpcore2.connection', 'httpcore2.http11',
                  'httpcore2.http2', 'httpcore2.proxy', 'httpcore2.socks'))
    loggers = [logging.getLogger(name) for name in names if name == 'client'
               or name.startswith(('mcp.', 'httpx2', 'httpcore2'))]
    guard = _SafeLibraryLogs()
    for target in loggers:
        target.addFilter(guard)
    token = _in_connection.set(True)
    try:
        yield
    finally:
        _in_connection.reset(token)
        for target in loggers:
            target.removeFilter(guard)


def _environment_value(name, environment=None):
    value = (os.environ if environment is None else environment).get(name)
    if value is None or '\0' in value:
        raise ValueError('mcp_environment_reference_unavailable')
    return value


@asynccontextmanager
async def _stdio_transport(settings, environment=None):
    from mcp.client.stdio import StdioServerParameters, stdio_client
    environment = {name: _environment_value(reference[2:-1], environment) for name, reference in settings.env.items()}
    params = StdioServerParameters(command=settings.command, args=settings.args,
                                   cwd=settings.cwd, env=environment)
    read_fd, write_fd = os.pipe()
    writer = os.fdopen(write_fd, 'w', encoding='utf-8')
    count = 0
    tail = bytearray()
    read_errors = []

    def drain():
        nonlocal count
        try:
            with os.fdopen(read_fd, 'rb', buffering=0) as reader:
                while chunk := reader.read(4096):
                    count += len(chunk)
                    tail.extend(chunk)
                    del tail[:-4096]
        except OSError as error:
            read_errors.append(type(error).__name__)

    # A real OS pipe works with SDK subprocesses on both POSIX and Windows;
    # a dedicated draining thread avoids platform-specific asyncio pipe APIs.
    thread = threading.Thread(target=drain, name='mcp-stderr', daemon=True)
    thread.start()
    try:
        async with stdio_client(params, errlog=writer) as streams:
            yield streams
    finally:
        writer.close()
        await asyncio.to_thread(thread.join, 5)
        logger.debug('mcp_stdio_stderr bytes=%d retained_bytes=%d', count, len(tail))
        tail.clear()
        if thread.is_alive() or read_errors:
            logger.error('mcp_stdio_diagnostic_drain_failed')
            raise ValueError('mcp_stdio_diagnostic_drain_failed')


class _LimitedJSONStream(httpx2.AsyncByteStream):
    def __init__(self, inner, limit):
        self.inner, self.limit = inner, limit

    async def __aiter__(self):
        count = 0
        async for chunk in self.inner:
            count += len(chunk)
            if count > self.limit:
                raise httpx2.ReadError('mcp_response_budget_exceeded')
            yield chunk

    async def aclose(self):
        await self.inner.aclose()


class _BudgetTransport(httpx2.AsyncBaseTransport):
    def __init__(self, proxy, limit):
        # Explicit proxy selection preserves auto/direct/mandatory proxy policy.
        self.inner = httpx2.AsyncHTTPTransport(proxy=proxy, verify=True, trust_env=True)
        self.limit = limit

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if response.headers.get('content-encoding', 'identity').lower().strip() != 'identity':
            await response.aclose()
            raise httpx2.ReadError('mcp_compressed_response_unsupported')
        if response.headers.get('content-type', '').split(';')[0].strip() == 'application/json':
            return httpx2.Response(response.status_code, headers=response.headers,
                stream=_LimitedJSONStream(response.stream, self.limit), extensions=response.extensions)
        return response

    async def aclose(self):
        await self.inner.aclose()


@asynccontextmanager
async def _http_transport(settings, network, environment=None):
    from mcp.client.streamable_http import streamable_http_client
    headers = {name: _environment_value(reference, environment) for name, reference in settings.headers_env.items()}
    if any(any(ord(c) < 32 or ord(c) == 127 for c in value) for value in headers.values()):
        raise ValueError('mcp_invalid_credential_header')
    headers['Accept-Encoding'] = 'identity'
    proxy = selected_proxy(settings.url, network)
    if proxy and urlsplit(proxy).scheme not in ('http', 'https'):
        raise ValueError('mcp_unsupported_proxy')
    async with httpx2.AsyncClient(transport=_BudgetTransport(proxy, settings.max_response_bytes),
            headers=headers, timeout=None, trust_env=False, follow_redirects=False) as http:
        # SDK 2.3 handles only redirects within the original endpoint's origin.
        async with streamable_http_client(settings.url, http_client=http,
                max_sse_event_size=settings.max_response_bytes) as streams:
            yield streams


@asynccontextmanager
async def open_connection(settings, network, *, environment=None):
    # Revalidate mutable nested containers before they reach the IO boundary.
    settings = MCPServerSettings.model_validate(settings.model_dump())
    if not settings.enabled:
        raise ValueError('mcp_server_disabled')
    from mcp.client.client import Client
    transport = (_stdio_transport(settings, environment) if settings.transport == 'stdio'
                 else _http_transport(settings, network, environment))
    with _safe_library_logs():
        async with Client(transport, read_timeout_seconds=settings.read_timeout_seconds or None,
                          cache=None, input_required_max_rounds=0) as client:
            yield client
