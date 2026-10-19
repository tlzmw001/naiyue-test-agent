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
    from naiyue_test_agent.tools.web_network import await_request
    async def scenario():
        started, settled = asyncio.Event(), asyncio.Event()
        async def pending():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()
        with pytest.raises(TimeoutError):
            await await_request(pending, .05, None)
        assert started.is_set() and settled.is_set()
        started.clear()
        settled.clear()
        signal = Event()
        class Control:
            @property
            def requested(self):
                return signal.is_set()
        task = asyncio.create_task(await_request(pending, 0, Control()))
        await started.wait()
        signal.set()
        with pytest.raises(ToolException, match='stopped'):
            await task
        assert settled.is_set()
        assert await await_request(lambda: asyncio.sleep(0, result=42), 0, None) == 42
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
