"""Transport policies are checked before credentials or response bytes escape."""

import asyncio
import logging

import httpx2
import pytest


def test_missing_stdio_environment_reference_does_not_spawn(tmp_path, monkeypatch):
    from naiyue_test_agent.mcp_client.discovery import discover_server, MCPDiscoveryError
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    monkeypatch.delenv('MCP_MISSING_FIXTURE_ENV', raising=False)
    from mcp.client import stdio
    monkeypatch.setattr(stdio, 'stdio_client', lambda *a, **kw: pytest.fail('must not spawn'))
    with pytest.raises(MCPDiscoveryError):
        asyncio.run(discover_server('local', MCPServerSettings(enabled=True, transport='stdio',
            command='unused', env={'KEY': '${MCP_MISSING_FIXTURE_ENV}'})))


def test_credentials_proxy_and_tls_use_explicit_policy(monkeypatch):
    from naiyue_test_agent.mcp_client import connections
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    from mcp.client import streamable_http
    from contextlib import asynccontextmanager
    seen = []
    monkeypatch.setenv('MCP_TEST_TOKEN', 'secret-token')
    original = httpx2.AsyncHTTPTransport
    def transport(**kwargs):
        seen.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(httpx2, 'AsyncHTTPTransport', transport)
    @asynccontextmanager
    async def sdk(url, *, http_client, **kwargs):
        assert http_client.headers['Authorization'] == 'secret-token'
        assert http_client.headers['Accept-Encoding'] == 'identity'
        assert http_client.timeout.read is None and not http_client.follow_redirects
        yield 'fixture-streams'
    monkeypatch.setattr(streamable_http, 'streamable_http_client', sdk)
    async def scenario():
        settings = MCPServerSettings(enabled=True, transport='streamable_http', url='https://example.invalid/mcp',
                                     headers_env={'Authorization': 'MCP_TEST_TOKEN'})
        async with connections._http_transport(settings, NetworkSettings(mode='proxy', proxy='http://proxy.invalid:123')) as value:
            assert value == 'fixture-streams'
    asyncio.run(scenario())
    assert seen == [{'proxy': 'http://proxy.invalid:123', 'verify': True, 'trust_env': True}]


def test_compression_is_rejected_before_body_read():
    from naiyue_test_agent.mcp_client.connections import _BudgetTransport
    consumed, closed = [], []
    class Body(httpx2.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True)
            yield b'not-gzip'
        async def aclose(self):
            closed.append(True)
    async def scenario():
        transport = _BudgetTransport(None, 1024)
        await transport.inner.aclose()
        transport.inner = httpx2.MockTransport(lambda request: httpx2.Response(200,
            headers={'Content-Encoding': 'gzip'}, stream=Body()))
        async with httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx2.ReadError, match='compressed_response'):
                await client.get('https://example.invalid')
    asyncio.run(scenario())
    assert consumed == [] and closed == [True]


def test_cross_origin_redirect_is_not_followed(monkeypatch):
    from naiyue_test_agent.mcp_client import connections
    from naiyue_test_agent.mcp_client.discovery import discover_server, MCPDiscoveryError
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    requests = []
    class Transport(httpx2.MockTransport):
        def __init__(self, *args):
            def respond(request):
                requests.append(str(request.url))
                return httpx2.Response(307, headers={'location': 'https://other.invalid/credential-sink'})
            super().__init__(respond)
    monkeypatch.setattr(connections, '_BudgetTransport', Transport)
    with pytest.raises(MCPDiscoveryError):
        asyncio.run(discover_server('local', MCPServerSettings(enabled=True, transport='streamable_http',
                                                              url='https://example.invalid/mcp')))
    assert requests and set(requests) == {'https://example.invalid/mcp'}


def test_library_log_redaction_is_scoped_and_restored(caplog):
    from naiyue_test_agent.mcp_client.connections import _safe_library_logs
    target = logging.getLogger('mcp.client.streamable_http')
    with caplog.at_level(logging.DEBUG):
        with _safe_library_logs():
            try:
                raise ValueError('PRIVATE-EXCEPTION')
            except ValueError:
                target.exception('PRIVATE-BODY %s', 'PRIVATE-HEADER')
        target.debug('unrelated-public-message')
    assert 'PRIVATE' not in caplog.text
    assert 'unrelated-public-message' in caplog.text
    assert 'mcp_library_diagnostic' in caplog.text


@pytest.mark.parametrize('bypass', [False, True])
def test_mcp_resources_preserve_authenticated_proxy_and_apply_bypass(monkeypatch, bypass):
    from contextlib import asynccontextmanager
    from mcp.client import streamable_http
    from naiyue_test_agent.mcp_client import connections
    from naiyue_test_agent.mcp_client.resources import MCPSessionResources
    from naiyue_test_agent.mcp_client.settings import MCPSettings, MCPServerSettings
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    observed = []
    original = httpx2.AsyncHTTPTransport
    def transport(**kwargs):
        observed.append(kwargs['proxy'])
        return original(**kwargs)
    monkeypatch.setattr(httpx2, 'AsyncHTTPTransport', transport)
    @asynccontextmanager
    async def sdk(url, *, http_client, **kwargs):
        assert 'Proxy-Authorization' not in http_client.headers
        yield 'fixture-streams'
    monkeypatch.setattr(streamable_http, 'streamable_http_client', sdk)
    async def scenario():
        network = NetworkSettings(mode='proxy', proxy='http://proxy.invalid:8080',
            proxy_username='alice', proxy_password='p@ss#word', no_proxy=['.corp.example'] if bypass else [])
        resources = MCPSessionResources(MCPSettings(), network)
        other = MCPSessionResources(MCPSettings(), network.model_copy(update={'proxy_password': 'different'}))
        try:
            server = MCPServerSettings(enabled=True, transport='streamable_http', url='https://api.corp.example/mcp')
            other._salt = resources._salt
            assert resources._configuration('remote', server)[1] != other._configuration('remote', server)[1]
            async with connections._http_transport(server, resources._network) as result:
                assert result == 'fixture-streams'
        finally:
            await resources.aclose()
            await other.aclose()
    asyncio.run(scenario())
    assert observed == [None if bypass else 'http://alice:p%40ss%23word@proxy.invalid:8080']
