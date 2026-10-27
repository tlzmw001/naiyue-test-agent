"""Public search uses real feedback/permission boundaries and synthetic HTTP."""

from runtime_context_helpers import RunIdentity, ToolAuthorization, typed_context
import asyncio
import json

import httpx
import pytest

from tool_feedback_helpers import invoke_tool_feedback


def test_baidu_failure_falls_back_once_without_disclosing_provider_body(monkeypatch, caplog):
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    from naiyue_test_agent.tools import web_network
    monkeypatch.setenv('BAIDU_SEARCH_API_KEY', 'synthetic-baidu-key')
    monkeypatch.setenv('TAVILY_API_KEY', 'synthetic-tavily-key')
    seen = []
    def handle(request):
        seen.append(request)
        if 'baidubce' in request.url.host:
            return httpx.Response(429, json={'error': 'PRIVATE_PROVIDER_BODY'})
        body = json.loads(request.content)
        assert body['search_depth'] == 'basic' and body['auto_parameters'] is False
        assert body['include_answer'] is False and body['include_raw_content'] is False
        return httpx.Response(200, json={'results': [
            {'title': 'Python', 'url': 'https://docs.python.org/', 'content': 'Official docs'}]})
    monkeypatch.setattr(web_network, 'open_client', lambda *a, **k:
                        httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    result = asyncio.run(invoke_tool_feedback(create_web_search_tool(), {'query': 'python'}, call_id='search'))
    assert result.status == 'success' and 'Official docs' in result.content
    assert 'tavily' in result.content and 'baidu: http_429' in result.content
    assert len(seen) == 2
    assert 'PRIVATE_PROVIDER_BODY' not in result.content + caplog.text
    assert 'synthetic-baidu-key' not in result.content + caplog.text


@pytest.mark.parametrize('first', ['success', 'empty', 'invalid', 'missing'])
def test_search_stops_after_success_or_tries_backup(monkeypatch, first):
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    from naiyue_test_agent.tools import web_network
    if first != 'missing':
        monkeypatch.setenv('BAIDU_SEARCH_API_KEY', 'synthetic')
    else:
        monkeypatch.delenv('BAIDU_SEARCH_API_KEY', raising=False)
    monkeypatch.setenv('TAVILY_API_KEY', 'synthetic')
    seen = []
    def handle(request):
        seen.append(request.url.host)
        if 'baidubce' in request.url.host:
            if first == 'invalid':
                return httpx.Response(200, json={'unexpected': []})
            return httpx.Response(200, json={'references': [] if first == 'empty' else [
                {'title': 'Baidu doc', 'url': 'https://example.org/doc', 'content': 'Baidu snippet'}]})
        return httpx.Response(200, json={'results': [
            {'title': 'Backup', 'url': 'https://example.org/backup', 'content': 'Tavily snippet'}]})
    monkeypatch.setattr(web_network, 'open_client', lambda *a, **k:
                        httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    result = asyncio.run(invoke_tool_feedback(create_web_search_tool(), {'query': 'docs'}, call_id='search'))
    assert result.status == 'success'
    assert len(seen) == (1 if first in ('success', 'missing') else 2)
    assert ('Baidu snippet' if first == 'success' else 'Tavily snippet') in result.content


def test_both_empty_is_success_both_errors_is_tool_error(monkeypatch):
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    from naiyue_test_agent.tools import web_network
    for key in ('BAIDU_SEARCH_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'synthetic')
    for status in (200, 401):
        def handle(request):
            return httpx.Response(status, json={'references': [], 'results': []})
        monkeypatch.setattr(web_network, 'open_client', lambda *a, **k:
                            httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        result = asyncio.run(invoke_tool_feedback(create_web_search_tool(), {'query': 'docs'}, call_id='search'))
        assert result.status == ('success' if status == 200 else 'error')
        assert ('No results' if status == 200 else 'http_401') in result.content


def test_total_deadline_reserves_fallback_and_zero_retains_attempt_cap(monkeypatch):
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    from naiyue_test_agent.tools import web_network
    for key in ('BAIDU_SEARCH_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'synthetic')
    observed = []
    from naiyue_test_agent.tools import retry
    from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
    monkeypatch.setattr(retry, 'monotonic', lambda: 0)
    original = retry.await_attempt
    async def record(call, seconds, control):
        observed.append(seconds)
        return await original(call, seconds, control)
    monkeypatch.setattr(retry, 'await_attempt', record)
    monkeypatch.setattr(web_network, 'open_client', lambda *a, **k: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(429))))
    for args in ({'query': 'docs'}, {'query': 'docs', 'timeout': 0}):
        result = asyncio.run(invoke_tool_feedback(create_web_search_tool(
            retry_settings=ToolRetrySettings(max_retries=0)), args, call_id='search'))
        assert result.status == 'error'
    assert observed == [15, 30, 30, 30]


def test_stop_during_first_provider_settles_without_starting_second(monkeypatch):
    from threading import Event
    from types import SimpleNamespace
    from langchain_core.tools import ToolException
    from naiyue_test_agent.tools.web_search import create_web_search_tool
    from naiyue_test_agent.tools import web_network
    from naiyue_test_agent.tools.permissions import execution_environment
    for key in ('BAIDU_SEARCH_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'synthetic')
    async def scenario():
        started, settled = asyncio.Event(), asyncio.Event()
        signal = Event()
        seen = []
        async def handle(request):
            seen.append(request.url.host)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()
        monkeypatch.setattr(web_network, 'open_client', lambda *a, **k:
                            httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        class Control:
            @property
            def requested(self):
                return signal.is_set()
        context = typed_context(identity=RunIdentity(session_id='s'), authorization=ToolAuthorization(permit={'session_id': 's', 'agent_path': 'root', 'call_id': 'c', 'subject': {'execution_environment': execution_environment()}}), control=Control())
        runtime = SimpleNamespace(context=context, tool_call_id='c', stream_writer=None)
        task = asyncio.create_task(create_web_search_tool().tool.coroutine('docs', runtime, timeout=0))
        await started.wait()
        signal.set()
        with pytest.raises(ToolException, match='stopped'):
            await task
        assert settled.is_set() and len(seen) == 1
    asyncio.run(scenario())
