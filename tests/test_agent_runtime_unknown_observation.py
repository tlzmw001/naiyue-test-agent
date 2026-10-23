"""Observe both kinds of unknown without changing Agent routing."""

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import logging
from pathlib import Path

import anthropic
import httpx
import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from openai import BadRequestError
from ollama import ResponseError

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def build_app(monkeypatch, model, tools=None):
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    return await build_application(
        Path('synthetic.json'), tools=tools or [], system_prompt='', response_format=None,
    )


@pytest.mark.parametrize(('failure', 'expected_status'), [
    (RuntimeError('private-exception-body'), None),
    (BadRequestError(
        'private-exception-body',
        response=httpx.Response(400, request=httpx.Request(
            'POST', 'https://example.invalid/private-endpoint')),
        body={'error': 'private-response-body'},
    ), 400),
    (anthropic.BadRequestError(
        'private-exception-body',
        response=httpx.Response(400, request=httpx.Request(
            'POST', 'https://example.invalid/private-endpoint')),
        body={'error': 'private-response-body'},
    ), 400),
    (ResponseError('private-exception-body', -1), None),
])
@async_test
async def test_unknown_exception_has_safe_diagnostic_and_still_fails_run(
    monkeypatch, caplog, failure, expected_status,
):
    class FailingFake(ToolCapableFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise failure

    app = await build_app(monkeypatch, FailingFake(responses=[]))
    session_id = await app.sessions.create('A')

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(type(failure)) as caught:
            await app.runs.start(session_id, [('user', 'private-prompt')])

    assert caught.value is failure
    events = [record.model_failure for record in caplog.records
              if hasattr(record, 'model_failure')]
    assert len(events) == 1
    assert events[0]['category'] == 'unknown'
    assert events[0]['reason'] == 'unclassified'
    assert events[0]['error_type'] == type(failure).__name__
    assert events[0].get('status_code') == expected_status
    run_events = [record.run_event for record in caplog.records
                  if hasattr(record, 'run_event')]
    assert [event['event'] for event in run_events] == ['run_started', 'run_failed']
    assert events[0]['run_id'] == run_events[0]['run_id'] == run_events[1]['run_id']
    assert run_events[0]['session_id'] == session_id
    for secret in ('private-prompt', 'private-exception-body', 'private-response-body',
                   'private-endpoint'):
        assert secret not in caplog.text + repr(events)


@pytest.mark.parametrize(('metadata', 'source', 'code', 'redacted'), [
    ({'finish_reason': 'future_reason'}, 'finish_reason', 'future_reason', False),
    ({}, 'missing', None, False),
    ({'status': 'future_status', 'finish_reason': 'stop'}, 'status', 'future_status', False),
    ({'finish_reason': 'sk_test_secret_marker'}, 'finish_reason', None, True),
    ({'finish_reason': 'sk_abcdef'}, 'finish_reason', None, True),
    ({'finish_reason': 'a' * 100}, 'finish_reason', None, True),
    ({'finish_reason': 123}, 'finish_reason', None, True),
])
@async_test
async def test_unknown_stop_reason_records_safe_source_without_changing_result(
    monkeypatch, caplog, metadata, source, code, redacted,
):
    model = ToolCapableFake(responses=[
        AIMessage(content='private-model-text', response_metadata=metadata),
    ])
    app = await build_app(monkeypatch, model)
    session_id = await app.sessions.create('A')

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = await app.runs.start(session_id, [('user', 'private-prompt')])

    assert result['messages'][-1].content == 'private-model-text'
    observed = [record.model_response for record in caplog.records
                if hasattr(record, 'model_response')]
    assert len(observed) == 1
    event = observed[0]
    assert event['event'] == 'model_stop_reason_unknown'
    assert event['model_id'] == 'A'
    assert event['normalized_reason'] == 'unknown'
    assert event['reason_source'] == source
    assert event['native_reason_code'] == code
    assert event['native_reason_redacted'] is redacted
    assert event['valid_tool_call_count'] == 0
    assert event['invalid_tool_call_count'] == 0
    run_events = [record.run_event for record in caplog.records
                  if hasattr(record, 'run_event')]
    assert [item['event'] for item in run_events] == ['run_started', 'run_completed']
    assert event['run_id'] == run_events[0]['run_id'] == run_events[1]['run_id']
    assert run_events[0]['session_id'] == session_id
    for secret in ('private-model-text', 'private-prompt', 'sk_test_secret_marker',
                   'sk_abcdef',
                   'a' * 100):
        assert secret not in caplog.text + repr(event)


@async_test
async def test_unknown_stop_reason_with_valid_tool_still_routes_tool(
    monkeypatch, caplog,
):
    calls = []

    @tool
    def probe(value: str) -> str:
        """Record one synthetic tool call."""
        calls.append(value)
        return 'ok'

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'probe', 'args': {'value': 'private-arg'}, 'id': 'call-1',
        }], response_metadata={'finish_reason': 'future_reason'}),
        AIMessage(content='done', response_metadata={'finish_reason': 'stop'}),
    ])
    app = await build_app(monkeypatch, model, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = await app.runs.start(await app.sessions.create('A'), [('user', 'probe')])

    assert result['messages'][-1].content == 'done'
    assert calls == ['private-arg']
    observed = [record.model_response for record in caplog.records
                if hasattr(record, 'model_response')]
    assert len(observed) == 1
    assert observed[0]['valid_tool_call_count'] == 1
    assert observed[0]['invalid_tool_call_count'] == 0
    assert [record.run_event['event'] for record in caplog.records
            if hasattr(record, 'run_event')] == ['run_started', 'run_completed']
    assert 'private-arg' not in caplog.text + repr(observed)


@async_test
async def test_unknown_stop_reason_is_observed_before_invalid_call_rejection(monkeypatch, caplog):
    calls = []

    @tool
    def probe(value: str) -> str:
        """Record a synthetic tool call if it gets past the response gate."""
        calls.append(value)
        return 'ok'

    model = ToolCapableFake(responses=[AIMessage(
        content='',
        tool_calls=[{'name': 'probe', 'args': {'value': 'private-arg'}, 'id': 'good'}],
        invalid_tool_calls=[{'name': 'probe', 'args': '{private', 'id': 'bad',
                             'error': 'private-parse-error'}],
        response_metadata={'finish_reason': 'future_reason'},
    )])
    app = await build_app(monkeypatch, model, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(RuntimeError) as caught:
            await app.runs.start(await app.sessions.create('A'), [('user', 'probe')])

    assert caught.value.reason == 'invalid_tool_calls'
    assert calls == []
    observed = [record.model_response for record in caplog.records
                if hasattr(record, 'model_response')]
    assert len(observed) == 1
    assert observed[0]['invalid_tool_call_count'] == 1
    assert observed[0]['valid_tool_call_count'] == 1
    assert [record.run_event['event'] for record in caplog.records
            if hasattr(record, 'run_event')] == ['run_started', 'run_failed']
    for secret in ('private-parse-error', 'private-arg'):
        assert secret not in caplog.text + repr(observed)
