"""Network routing and owned request termination have deterministic tests."""

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException


def test_proxy_modes_respect_no_proxy_and_never_implicitly_go_direct(monkeypatch):
    from naiyue_test_agent.tools import web_network
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    monkeypatch.setattr(web_network, 'getproxies', lambda: {
        'https': 'http://proxy.invalid:8000', 'no': 'localhost,.internal'})
    assert web_network.selected_proxy('https://docs.python.org', NetworkSettings()) == 'http://proxy.invalid:8000'
    assert web_network.selected_proxy('http://localhost:8080', NetworkSettings()) is None
    assert web_network.selected_proxy('https://docs.python.org', NetworkSettings(mode='direct')) is None
    assert web_network.selected_proxy('http://localhost', NetworkSettings(mode='proxy', proxy='http://forced.invalid')) == 'http://forced.invalid'


def test_deadline_and_stop_settle_request_before_returning():
    from naiyue_test_agent.tools.retry import await_attempt
    async def scenario():
        started, settled = asyncio.Event(), asyncio.Event()
        async def pending():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()
        with pytest.raises(TimeoutError):
            await await_attempt(pending, .05, None)
        assert started.is_set() and settled.is_set()
        started.clear()
        settled.clear()
        signal = Event()
        class Control:
            @property
            def requested(self):
                return signal.is_set()
        task = asyncio.create_task(await_attempt(pending, 0, Control()))
        await started.wait()
        signal.set()
        with pytest.raises(ToolException, match='stopped'):
            await task
        assert settled.is_set()
        assert await await_attempt(lambda: asyncio.sleep(0, result=42), 0, None) == 42
    asyncio.run(scenario())


def test_tls_context_preserves_hostname_when_proxy_transport_passes_pinned_ip(monkeypatch):
    import ssl
    from naiyue_test_agent.tools.web_network import target_tls_context
    context = target_tls_context('docs.example.org')
    seen = []
    monkeypatch.setattr(ssl.SSLContext, 'wrap_bio', lambda self, *args, **kwargs: seen.append(kwargs))
    context.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname='93.184.216.34')
    assert seen == [{'server_hostname': 'docs.example.org'}]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_compressed_response_is_rejected_before_decoding_or_reading():
    import httpx
    from naiyue_test_agent.tools.web_network import download
    consumed = []
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True)
            yield b'not-even-valid-gzip'
    async def scenario():
        transport = httpx.MockTransport(lambda _: httpx.Response(200,
            headers={'Content-Encoding': 'gzip'}, stream=Body()))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match='content_encoding'):
                await download(client, 'GET', 'https://example.org', max_bytes=1024)
    asyncio.run(scenario())
    assert consumed == []


def test_forced_plain_http_proxy_client_can_be_constructed_without_io():
    from naiyue_test_agent.tools.web_network import open_client
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    async def scenario():
        settings = NetworkSettings(mode='proxy', proxy='http://127.0.0.1:7890')
        async with open_client('https://example.org', settings, target_hostname='example.org') as client:
            assert client.headers['Accept-Encoding'] == 'identity'
    asyncio.run(scenario())


@pytest.mark.parametrize('target, direct', [
    ('http://localhost:3000', True), ('http://127.0.0.1:8080', True),
    ('http://[::1]:8080', True), ('https://corp.example', True),
    ('https://API.CORP.EXAMPLE:444', True), ('https://corp.example.', True),
    ('https://notcorp.example', False), ('https://corp.example.evil.example', False),
    ('https://public.example', False),
])
@pytest.mark.parametrize('mode', ['proxy', 'auto'])
def test_configured_bypass_precedes_manual_and_environment_proxy(monkeypatch, target, direct, mode):
    from naiyue_test_agent.tools import web_network
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    monkeypatch.setattr(web_network, 'getproxies', lambda: {'https': 'http://environment.invalid'})
    settings = NetworkSettings(mode=mode, proxy='http://manual.invalid',
        no_proxy=['localhost', '127.0.0.1', '::1', '.corp.example'])
    expected = None if direct else 'http://manual.invalid' if mode == 'proxy' else 'http://environment.invalid'
    assert web_network.selected_proxy(target, settings) == expected


def test_separate_authentication_is_encoded_once_and_never_applied_to_environment(monkeypatch):
    from naiyue_test_agent.tools import web_network
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    import httpx
    fields = dict(proxy='http://proxy.invalid:8080', proxy_username='alice', proxy_password='p@ss#:/%word')
    settings = NetworkSettings(mode='proxy', **fields)
    selected = web_network.selected_proxy('https://public.example', settings)
    assert selected == 'http://alice:p%40ss%23%3A%2F%25word@proxy.invalid:8080'
    assert httpx.Proxy(selected).auth == ('alice', 'p@ss#:/%word')
    assert settings.proxy_password == fields['proxy_password']
    monkeypatch.setattr(web_network, 'getproxies', lambda: {'https': 'http://environment.invalid'})
    assert web_network.selected_proxy('https://public.example', NetworkSettings(mode='auto', **fields)) == 'http://environment.invalid'
    assert web_network.selected_proxy('https://public.example', NetworkSettings(mode='direct', **fields)) is None


@pytest.mark.parametrize('url, direct', [
    ('http://foo.127.0.0.1', False), ('http://127.0.0.1', True),
    ('http://[0:0:0:0:0:0:0:1]', True), ('http://public.example', False),
])
def test_ip_bypass_entries_match_addresses_not_domain_suffixes(url, direct):
    from naiyue_test_agent.tools.web_network import selected_proxy
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    settings = NetworkSettings(mode='proxy', proxy='http://proxy.invalid', no_proxy=['127.0.0.1', '::1'])
    assert selected_proxy(url, settings) == (None if direct else settings.proxy)


def test_real_proxy_authentication_bypass_and_failure_never_go_direct(caplog):
    import base64
    import logging
    import httpx
    from naiyue_test_agent.tools.web_network import open_client
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    async def scenario():
        proxy_requests, direct_requests = [], []
        writers = set()
        async def serve(reader, writer, captured):
            writers.add(writer)
            try:
                captured.append(await reader.readuntil(b'\r\n\r\n'))
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                writers.discard(writer)
        async with (await asyncio.start_server(lambda r, w: serve(r, w, proxy_requests), '127.0.0.1', 0)) as proxy_server:
            async with (await asyncio.start_server(lambda r, w: serve(r, w, direct_requests), '127.0.0.1', 0)) as direct_server:
                proxy_port = proxy_server.sockets[0].getsockname()[1]
                direct_port = direct_server.sockets[0].getsockname()[1]
                settings = NetworkSettings(mode='proxy', proxy=f'http://127.0.0.1:{proxy_port}',
                    proxy_username='PRIVATE_USER', proxy_password='PRIVATE@PASSWORD#', no_proxy=['127.0.0.1'])
                async with open_client('http://public.invalid/document', settings) as client:
                    response = await client.get('http://public.invalid/document')
                    assert response.text == 'ok'
                direct_url = f'http://127.0.0.1:{direct_port}/direct'
                async with open_client(direct_url, settings) as client:
                    assert (await client.get(direct_url)).text == 'ok'
                assert len(proxy_requests) == len(direct_requests) == 1
                token = base64.b64encode(b'PRIVATE_USER:PRIVATE@PASSWORD#')
                assert b'Proxy-Authorization: Basic ' + token in proxy_requests[0]
                assert b'Proxy-Authorization' not in direct_requests[0]
                proxy_server.close()
                await proxy_server.wait_closed()
                unavailable = settings.model_copy(update={'no_proxy': ()})
                with pytest.raises(httpx.ConnectError):
                    async with open_client(direct_url, unavailable) as client:
                        await client.get(direct_url)
                assert len(proxy_requests) == len(direct_requests) == 1
        assert not writers
    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())
    assert 'PRIVATE_USER' not in caplog.text and 'PRIVATE@PASSWORD#' not in caplog.text
