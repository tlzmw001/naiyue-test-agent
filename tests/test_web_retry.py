"""Retries preserve reviewed targets, provider fallback and one logical result."""

import asyncio
import socket

import httpx
import pytest

from naiyue_test_agent.tools import web_network
from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
from tool_feedback_helpers import invoke_tool_feedback


def web_fixture(monkeypatch, handler):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    monkeypatch.setattr(web_network, 'open_client', lambda *a, **k:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    for key in ('BAIDU_SEARCH_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'synthetic')


@pytest.mark.parametrize('status', [429, 500, 502, 503, 504])
def test_fetch_retries_transient_status_with_identical_reviewed_target(monkeypatch, status):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status if len(requests) == 1 else 200, text='ok')
    web_fixture(monkeypatch, handle)
    spec = create_web_fetch_tool(retry_settings=ToolRetrySettings(initial_delay_seconds=0))
    result = asyncio.run(invoke_tool_feedback(spec, {'url': 'https://example.org/docs'}, call_id='fetch'))
    assert result.status == 'success' and len(requests) == 2
    assert all(r.url.host == '93.184.216.34' and r.headers['Host'] == 'example.org' for r in requests)


@pytest.mark.parametrize('status', [400, 401, 403, 404, 407, 501])
def test_fetch_does_not_retry_permanent_status(monkeypatch, status):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(status, text='PRIVATE_RESPONSE')
    web_fixture(monkeypatch, handle)
    result = asyncio.run(invoke_tool_feedback(create_web_fetch_tool(), {'url': 'https://example.org'}, call_id='f'))
    assert result.status == 'error' and len(seen) == 1 and 'PRIVATE_RESPONSE' not in result.content


def test_fetch_retry_exhaustion_is_error_feedback_and_reuses_original_permission(monkeypatch):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    requests = []
    def handle(request):
        requests.append(request)
        raise httpx.ReadError('PRIVATE_TRANSPORT')
    web_fixture(monkeypatch, handle)
    result = asyncio.run(invoke_tool_feedback(create_web_fetch_tool(
        retry_settings=ToolRetrySettings(initial_delay_seconds=0)), {'url': 'https://example.org'}, call_id='f'))
    assert result.status == 'error' and len(requests) == 3
    assert 'PRIVATE_TRANSPORT' not in result.content


def test_search_tries_fallback_before_retrying_failed_provider(monkeypatch):
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    seen = []
    def handle(request):
        seen.append(request.url.host)
        if len(seen) <= 2:
            return httpx.Response(503)
        return httpx.Response(200, json={'references': [{'title': 'Docs', 'url': 'https://example.org', 'content': 'ok'}]})
    web_fixture(monkeypatch, handle)
    result = asyncio.run(invoke_tool_feedback(create_web_search_tool(
        retry_settings=ToolRetrySettings(initial_delay_seconds=0)), {'query': 'docs'}, call_id='s'))
    assert result.status == 'success'
    assert seen == ['qianfan.baidubce.com', 'api.tavily.com', 'qianfan.baidubce.com']


@pytest.mark.parametrize('retries, expected', [(0, 2), (1, 3), (2, 4)])
def test_search_retry_allowance_is_shared_and_fallback_does_not_consume_it(monkeypatch, retries, expected):
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    seen = []
    def handle(request):
        seen.append(request.url.host)
        return httpx.Response(503)
    web_fixture(monkeypatch, handle)
    result = asyncio.run(invoke_tool_feedback(create_web_search_tool(retry_settings=ToolRetrySettings(
        max_retries=retries, initial_delay_seconds=0)), {'query': 'docs'}, call_id='s'))
    assert result.status == 'error' and len(seen) == expected
    assert seen[:2] == ['qianfan.baidubce.com', 'api.tavily.com']


def test_retry_after_on_first_provider_does_not_delay_fallback(monkeypatch):
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    seen = []
    def handle(request):
        seen.append(request.url.host)
        if len(seen) == 1:
            return httpx.Response(429, headers={'Retry-After': '120'})
        return httpx.Response(200, json={'results': [{'title': 'Docs', 'url': 'https://example.org', 'content': 'ok'}]})
    web_fixture(monkeypatch, handle)
    result = asyncio.run(invoke_tool_feedback(create_web_search_tool(), {'query': 'docs', 'timeout': 1}, call_id='s'))
    assert result.status == 'success' and len(seen) == 2


def test_empty_provider_is_not_retried_but_failed_provider_can_recover(monkeypatch):
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    seen = []
    def handle(request):
        seen.append(request.url.host)
        if len(seen) == 1:
            return httpx.Response(200, json={'references': []})
        if len(seen) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={'results': [{'title': 'Docs', 'url': 'https://example.org', 'content': 'ok'}]})
    web_fixture(monkeypatch, handle)
    result = asyncio.run(invoke_tool_feedback(create_web_search_tool(retry_settings=ToolRetrySettings(
        initial_delay_seconds=0)), {'query': 'docs'}, call_id='s'))
    assert result.status == 'success'
    assert seen == ['qianfan.baidubce.com', 'api.tavily.com', 'api.tavily.com']


def test_total_timeout_is_not_reset_when_switching_providers(monkeypatch):
    from naiyue_test_agent.tools import retry
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    now, seen = [0.0], []
    monkeypatch.setattr(retry, 'monotonic', lambda: now[0])
    def handle(request):
        seen.append(request.url.host)
        now[0] += 6
        return httpx.Response(503)
    web_fixture(monkeypatch, handle)
    result = asyncio.run(invoke_tool_feedback(create_web_search_tool(retry_settings=ToolRetrySettings(
        initial_delay_seconds=0)), {'query': 'docs', 'timeout': 10}, call_id='s'))
    assert result.status == 'error' and 'timed out' in result.content
    assert len(seen) == 2


def test_search_proxy_authentication_failure_does_not_fallback(monkeypatch):
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(407, text='PRIVATE')
    web_fixture(monkeypatch, handle)
    result = asyncio.run(invoke_tool_feedback(create_web_search_tool(), {'query': 'docs'}, call_id='s'))
    assert result.status == 'error' and len(seen) == 1 and 'PRIVATE' not in result.content


def test_retry_after_past_total_deadline_stops_without_another_fetch(monkeypatch):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(429, headers={'Retry-After': '120'})
    web_fixture(monkeypatch, handle)
    result = asyncio.run(invoke_tool_feedback(create_web_fetch_tool(),
        {'url': 'https://example.org', 'timeout': 1}, call_id='f'))
    assert result.status == 'error' and len(seen) == 1 and 'HTTP 429' in result.content


def test_fetch_retry_rechecks_redirect_permission_and_never_follows_private_target(monkeypatch):
    from naiyue_test_agent.tools.web_fetch import create_web_fetch_tool
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(503) if len(seen) == 1 else httpx.Response(302, headers={'Location': 'http://127.0.0.1/private'})
    web_fixture(monkeypatch, handle)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, *a, **k:
        [(2, 1, 6, '', ('127.0.0.1' if host == '127.0.0.1' else '93.184.216.34', 443))])
    result = asyncio.run(invoke_tool_feedback(create_web_fetch_tool(
        retry_settings=ToolRetrySettings(initial_delay_seconds=0)), {'url': 'https://example.org'}, call_id='f'))
    assert result.status == 'error' and len(seen) == 2 and 'separate approval' in result.content
    assert all(r.url.host == '93.184.216.34' for r in seen)
