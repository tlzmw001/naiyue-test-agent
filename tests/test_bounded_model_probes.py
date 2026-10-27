"""Current probe workers install limits on owned real adapters with mocked IO."""

import asyncio
import json
import logging
import re
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from langchain_openai import ChatOpenAI

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from scripts import provider_acceptance_budget as limits


def install_transport(monkeypatch, *, extra_tool=False):
    attempts, guarded = [], []
    model = ChatOpenAI(model='synthetic', api_key='synthetic', base_url='https://example.invalid/v1',
        max_retries=2, use_responses_api=False, metadata={'naiyue_model_id': 'main', 'naiyue_model_limits': {
            'model_id': 'main', 'context_window_tokens': 128000, 'max_output_tokens': 1024,
            'context_window_source': 'config', 'max_output_source': 'config'},
            'naiyue_compaction_reserve': {'tokens': 16384, 'source': 'config'},
            'naiyue_compaction_keep_recent': {'tokens': 4096, 'source': 'config'},
            'naiyue_compaction_summary_output': {'tokens': 64, 'source': 'config'}})
    catalog = ModelCatalog('main', {'main': model})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
    monkeypatch.setattr('naiyue_test_agent.model_access.load_model_catalog', lambda _: catalog)
    monkeypatch.setattr(application, 'load_settings', AppSettings)
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    original = limits.guard_catalog

    def guard(cat, budget):
        original(cat, budget)
        guarded.append(budget)

    monkeypatch.setattr(limits, 'guard_catalog', guard)

    async def reply(transport, request):
        payload = json.loads(request.content)
        attempts.append(payload)
        messages = payload['messages']
        tool_results = [m for m in messages if m['role'] == 'tool']
        if payload.get('tools') and (not tool_results or extra_tool):
            message = {'role': 'assistant', 'content': 'CLI_BEFORE_TOOL', 'tool_calls': [{
                'id': f'call-{len(attempts)}', 'type': 'function',
                'function': {'name': 'read_probe_value', 'arguments': '{}'}}]}
            reason = 'tool_calls'
        else:
            text = '\n'.join(str(message.get('content', '')) for message in messages)
            marker = re.search(r'\b[0-9a-f]{32}\b', text)
            content = tool_results[-1]['content'] if tool_results else marker.group() if marker else 'SAVED'
            message, reason = {'role': 'assistant', 'content': content}, 'stop'
        response = httpx.Response if isinstance(request, httpx.Request) else httpx2.Response
        identity = f'chatcmpl-{len(attempts)}'
        if not payload.get('stream'):
            return response(200, json={'id': identity, 'model': 'synthetic', 'object': 'chat.completion',
                'choices': [{'index': 0, 'message': message, 'finish_reason': reason}]})
        delta = dict(message)
        if 'tool_calls' in delta:
            delta['tool_calls'] = [dict(call, index=i) for i, call in enumerate(delta['tool_calls'])]
        chunks = [dict(id=identity, model='synthetic', object='chat.completion.chunk',
                       choices=[{'index': 0, 'delta': delta, 'finish_reason': None}]),
                  dict(id=identity, model='synthetic', object='chat.completion.chunk',
                       choices=[{'index': 0, 'delta': {}, 'finish_reason': reason}])]
        body = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
        return response(200, headers={'content-type': 'text/event-stream'}, content=body.encode())

    for module in (httpx, httpx2):
        original_request = module.AsyncHTTPTransport.handle_async_request
        async def provider_only(transport, request, original_request=original_request):
            # The terminal now uses real loopback HTTP; only provider IO is mocked.
            if request.url.host in ('127.0.0.1', '::1', 'localhost'):
                return await original_request(transport, request)
            return await reply(transport, request)
        monkeypatch.setattr(module.AsyncHTTPTransport, 'handle_async_request', provider_only)
    return catalog, attempts, guarded


@pytest.mark.parametrize('kind,expected', [('configured', 2), ('events', 2), ('cli', 2),
                                          ('async', 1), ('context', 2)])
def test_formal_probe_workers_use_http_budget_and_close_clients(monkeypatch, capsys, kind, expected):
    catalog, attempts, guarded = install_transport(monkeypatch)
    previous = logging.getLogger().level
    try:
        if kind == 'configured':
            from scripts import probe_configured_tool_loop as probe
            monkeypatch.setattr('sys.argv', ['probe', '--config', 'unused', '--model-id', 'main'])
            monkeypatch.setattr(probe.signal, 'signal', lambda *_: None)
            monkeypatch.setattr(probe.signal, 'alarm', lambda *_: 0)
            assert asyncio.run(probe.main()) == 0
        elif kind in ('events', 'cli'):
            from scripts.probe_message_events import _worker
            _worker(SimpleNamespace(config='unused', model_id='main', with_control=False, with_cli=kind == 'cli'))
        else:
            from scripts.probe_async_model import _worker
            asyncio.run(_worker(SimpleNamespace(config='unused', model_id='main',
                timeout_seconds=5, with_context=kind == 'context')))
    finally:
        logging.getLogger().setLevel(previous)
    assert 'PASS' in capsys.readouterr().out
    assert len(attempts) == expected and len(guarded) == 1
    assert guarded[0].snapshot()['used'] == expected
    assert catalog._owned_http_clients and all(client.is_closed for client, _ in catalog._owned_http_clients)
    assert catalog.get('main').max_retries == 0


def test_event_probe_third_call_is_blocked_before_transport_and_still_closes(monkeypatch):
    from scripts.probe_message_events import _worker
    catalog, attempts, guarded = install_transport(monkeypatch, extra_tool=True)
    previous = logging.getLogger().level
    try:
        with pytest.raises(limits.AcceptanceBudgetStop):
            _worker(SimpleNamespace(config='unused', model_id='main', with_control=False, with_cli=False))
    finally:
        logging.getLogger().setLevel(previous)
    assert len(attempts) == 2 and guarded[0].snapshot()['used'] == 2
    assert guarded[0].snapshot()['stopped'] is True
    assert all(client.is_closed for client, _ in catalog._owned_http_clients)


def test_real_persistence_worker_uses_one_http_request(monkeypatch, tmp_path, capsys):
    from scripts.probe_persistent_session import _worker
    catalog, attempts, guarded = install_transport(monkeypatch)
    previous = logging.getLogger().level
    try:
        _worker(SimpleNamespace(config='unused', real=True, worker='save', value='synthetic-value',
                                database=tmp_path / 'sessions.sqlite'))
    finally:
        logging.getLogger().setLevel(previous)
    assert json.loads(capsys.readouterr().out)['status'] == 'returned'
    assert len(attempts) == guarded[0].snapshot()['used'] == 1
    assert all(client.is_closed for client, _ in catalog._owned_http_clients)
