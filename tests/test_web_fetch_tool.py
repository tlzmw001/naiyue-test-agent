"""Public/private review, redirect permissions and pinned HTTP destinations."""

import asyncio
import socket

import httpx

from tool_feedback_helpers import invoke_tool_feedback, EXPECTED_NOASK


def test_fetch_public_html_pins_dns_and_excludes_script(monkeypatch):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    from naiyue_test_agent.tools import web_network
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, headers={'Content-Type': 'text/html; charset=utf-8'},
            text='<h1>Docs</h1><script>PRIVATE_SCRIPT</script><p>Read <a href="/guide">guide</a></p>')
    monkeypatch.setattr(web_network, 'open_client', lambda *a, **k:
                        httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    result = asyncio.run(invoke_tool_feedback(create_web_fetch_tool(), {'url': 'https://example.org/docs'}, call_id='fetch'))
    assert result.status == 'success' and 'Docs' in result.content
    assert 'https://example.org/guide' in result.content and 'PRIVATE_SCRIPT' not in result.content
    assert requests[0].url.host == '93.184.216.34'
    assert requests[0].headers['Host'] == 'example.org'
    assert requests[0].extensions['sni_hostname'] == 'example.org'


def test_private_fetch_waits_for_permission_without_http(monkeypatch):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    from naiyue_test_agent.tools import web_network
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 80))])
    def forbidden(*args, **kwargs):
        raise AssertionError('No HTTP before approval')
    monkeypatch.setattr(web_network, 'open_client', forbidden)
    result = asyncio.run(invoke_tool_feedback(create_web_fetch_tool(), {'url': 'http://localhost/'}, call_id='fetch'))
    assert result.status == 'error' and result.content == EXPECTED_NOASK


def test_public_redirect_does_not_inherit_permission_for_private_target(monkeypatch):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    from naiyue_test_agent.tools import web_network
    def resolve(host, *args, **kwargs):
        return [(2, 1, 6, '', ('127.0.0.1' if host == 'localhost' else '93.184.216.34', 80))]
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={'location': 'http://localhost/admin'})
    monkeypatch.setattr(web_network, 'open_client', lambda *a, **k:
                        httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    result = asyncio.run(invoke_tool_feedback(create_web_fetch_tool(), {'url': 'http://example.org/'}, call_id='fetch'))
    assert result.status == 'error' and 'separate approval' in result.content
    assert len(requests) == 1


def test_response_download_and_output_limits(monkeypatch):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    from naiyue_test_agent.tools.web_settings import WebFetchSettings
    from naiyue_test_agent.tools import web_network
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    monkeypatch.setattr(web_network, 'open_client', lambda *a, **k: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text='中' * 100))))
    spec = create_web_fetch_tool(settings=WebFetchSettings(max_response_bytes=10))
    result = asyncio.run(invoke_tool_feedback(spec, {'url': 'https://example.org/'}, call_id='fetch'))
    assert result.status == 'error' and 'budget' in result.content


def test_startup_registers_three_tools_with_configured_budgets():
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools
    settings = AppSettings()
    specs = {spec.tool.name: spec for spec in create_builtin_tools(settings.tools)}
    assert {'aitest_kit', 'web_search', 'web_fetch'} <= specs.keys()
    assert specs['web_search'].tool.coroutine is not None
    assert 'runtime' not in specs['aitest_kit'].tool.tool_call_schema.model_fields


def test_fetch_tries_another_reviewed_address_when_first_connection_fails(monkeypatch):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    from naiyue_test_agent.tools import web_network
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [
        (10, 1, 6, '', ('2606:4700:4700::1111', 443, 0, 0)),
        (2, 1, 6, '', ('93.184.216.34', 443))])
    hosts = []
    def handle(request):
        hosts.append(request.url.host)
        if ':' in request.url.host:
            raise httpx.ConnectError('no ipv6 route')
        return httpx.Response(200, text='reachable IPv4')
    monkeypatch.setattr(web_network, 'open_client', lambda *a, **k:
                        httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    result = asyncio.run(invoke_tool_feedback(create_web_fetch_tool(), {'url': 'https://example.org'}, call_id='fetch'))
    assert result.status == 'success' and 'reachable IPv4' in result.content
    assert hosts == ['2606:4700:4700::1111', '93.184.216.34']
