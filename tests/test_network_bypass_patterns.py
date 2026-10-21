"""Configured bypass patterns select a route without DNS or credential leakage."""

import asyncio

import pytest

from naiyue_test_agent.tools import web_network
from naiyue_test_agent.tools.web_settings import NetworkSettings


@pytest.mark.parametrize('pattern, expected', [
    ('10.1.2.3/8', '10.0.0.0/8'), ('fd12:3456::1/8', 'fd00::/8'),
    ('10.*', '10.0.0.0/8'), ('10.*.*.*', '10.0.0.0/8'),
    ('10.20.*', '10.20.0.0/16'), ('10.20.*.*', '10.20.0.0/16'),
    ('10.20.30.*', '10.20.30.0/24'), ('*.CORP.EXAMPLE.', '*.corp.example'),
    ('*.公司.example', '*.xn--55qx5d.example'), ('*', '*'),
])
def test_patterns_have_stable_canonical_configuration(pattern, expected):
    settings = NetworkSettings(no_proxy=[pattern])
    assert settings.no_proxy == (expected,)
    assert NetworkSettings.model_validate(dict(settings)) == settings


@pytest.mark.parametrize('pattern, host, direct', [
    ('10.0.0.0/8', '10.0.0.0', True), ('10.0.0.0/8', '10.255.255.255', True),
    ('10.*', '10.20.30.40', True), ('10.*', '11.0.0.1', False),
    ('10.20.*', '10.20.255.255', True), ('10.20.*', '10.21.0.0', False),
    ('10.20.30.*', '10.20.30.0', True), ('10.20.30.*', '10.20.31.0', False),
    ('10.20.30.40/32', '10.20.30.40', True), ('10.20.30.40/32', '10.20.30.41', False),
    ('0.0.0.0/0', '203.0.113.5', True), ('0.0.0.0/0', '[::1]', False),
    ('fd00::/8', '[fd12:3456::1]', True), ('fd00::/8', '[fe80::1]', False),
    ('::1/128', '[0:0:0:0:0:0:0:1]', True), ('::1/128', '[::2]', False),
    ('::/0', '[2001:db8::1]', True), ('::/0', '10.20.30.40', False),
    ('10.*', 'service.corp.example', False), ('10.*', 'foo.10.20.30.40', False),
    ('*.corp.example', 'api.corp.example', True),
    ('*.corp.example', 'deep.api.corp.example', True),
    ('*.corp.example', 'API.CORP.EXAMPLE.', True),
    ('*.corp.example', 'corp.example', False),
    ('*.corp.example', 'notcorp.example', False),
    ('*.corp.example', 'api.corp.example.evil', False),
    ('*.公司.example', 'api.xn--55qx5d.example', True),
    ('*', 'public.example', True), ('*', '10.20.30.40', True), ('*', '[::1]', True),
])
@pytest.mark.parametrize('mode', ['proxy', 'auto'])
def test_patterns_select_only_the_configured_hosts(monkeypatch, pattern, host, direct, mode):
    monkeypatch.setattr(web_network, 'getproxies', lambda: {'http': 'http://environment.invalid'})
    settings = NetworkSettings(mode=mode, proxy='http://manual.invalid', no_proxy=[pattern])
    expected = None if direct else settings.proxy if mode == 'proxy' else 'http://environment.invalid'
    assert web_network.selected_proxy(f'http://{host}:8080/document', settings) == expected


@pytest.mark.parametrize('pattern', [
    '10.0.0.0/33', 'fd00::/129', '10.0.0.0/-1', '10.0.0.0/',
    '10.0.0.0/255.0.0.0', 'corp.example/8', 'fe80::1%zone/64',
    '10.999.*', '10.020.*', '10.*.2.*', '10.2*', '10.*.*.*.*',
    '*.127.0.0.1', '*corp.example', 'api.*.example', '**.corp.example',
    '*.corp.example:8080', '*.corp.example/path', 'http://10.*',
    '10.0.0.0/8 ', ' 10.*', '*.corp..example', '*..corp.example', '?corp.example',
])
def test_invalid_patterns_still_fail_closed(pattern):
    with pytest.raises(ValueError):
        NetworkSettings(mode='proxy', proxy='http://PRIVATE:SECRET@proxy.invalid', no_proxy=[pattern])


def test_loading_patterns_keeps_credentials_private_and_does_not_resolve_dns(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.settings import load_settings
    monkeypatch.setattr(web_network.socket, 'getaddrinfo', lambda *a, **k: pytest.fail('must not resolve DNS'))
    path = tmp_path / 'settings.toml'
    path.write_text('[network]\nmode="proxy"\nproxy="http://proxy.invalid"\n'
        'proxy_username="PRIVATE"\nproxy_password="SECRET"\n'
        'no_proxy=["10.*", "fd00::/8", "*.corp.example"]\n')
    settings = load_settings(path).network
    assert settings.no_proxy == ('10.0.0.0/8', 'fd00::/8', '*.corp.example')
    assert web_network.selected_proxy('http://10.1.2.3', settings) is None
    assert web_network.selected_proxy('http://api.corp.example', settings) is None
    assert web_network.selected_proxy('http://public.example', settings).startswith('http://PRIVATE:SECRET@')
    assert 'PRIVATE' not in repr(settings) + str(settings.model_dump()) + caplog.text
    assert 'SECRET' not in repr(settings) + str(settings.model_dump()) + caplog.text


@pytest.mark.parametrize('pattern', ['127.0.0.0/8', '127.*'])
def test_real_http_client_bypasses_proxy_without_sending_proxy_credentials(pattern):
    async def scenario():
        requests, writers, settled = [], set(), asyncio.Event()
        async def serve(reader, writer):
            writers.add(writer)
            try:
                requests.append(await reader.readuntil(b'\r\n\r\n'))
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                writers.discard(writer)
                settled.set()
        async with await asyncio.start_server(serve, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            settings = NetworkSettings(mode='proxy', proxy='http://unreachable.invalid',
                proxy_username='PRIVATE', proxy_password='SECRET', no_proxy=[pattern])
            url = f'http://127.0.0.1:{port}/document'
            async with web_network.open_client(url, settings) as client:
                response = await client.get(url)
                assert response.status_code == 200 and response.text == 'ok'
            await settled.wait()
        assert len(requests) == 1 and b'Proxy-Authorization' not in requests[0]
        assert not writers
    asyncio.run(scenario())


@pytest.mark.parametrize('pattern, target, direct', [
    ('10.*', 'https://10.1.2.3/mcp', True),
    ('10.*', 'https://11.1.2.3/mcp', False),
    ('fd00::/8', 'https://[fd12::1]/mcp', True),
    ('fd00::/8', 'https://[2001:db8::1]/mcp', False),
    ('*.corp.example', 'https://api.corp.example/mcp', True),
    ('*.corp.example', 'https://corp.example/mcp', False),
    ('*', 'https://public.example/mcp', True),
])
def test_mcp_transport_consumes_the_same_bypass_policy(monkeypatch, pattern, target, direct):
    from contextlib import asynccontextmanager
    from mcp.client import streamable_http
    from naiyue_test_agent.mcp_client import connections
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    captured = []
    original = connections._BudgetTransport
    def transport(proxy, limit):
        captured.append(proxy)
        return original(proxy, limit)
    @asynccontextmanager
    async def streams(url, *, http_client, **kwargs):
        assert url == target and http_client is not None
        yield (None, None)
    monkeypatch.setattr(connections, '_BudgetTransport', transport)
    monkeypatch.setattr(streamable_http, 'streamable_http_client', streams)
    settings = NetworkSettings(mode='proxy', proxy='http://proxy.invalid', no_proxy=[pattern])
    server = MCPServerSettings(enabled=True, transport='streamable_http', url=target)
    async def scenario():
        async with connections._http_transport(server, settings, {}) as result:
            assert result == (None, None)
    asyncio.run(scenario())
    assert captured == [None if direct else settings.proxy]
