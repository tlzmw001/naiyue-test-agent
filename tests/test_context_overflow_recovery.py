"""Explicit input rejection recovers one model request without replaying tools."""

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import json
import logging

import anthropic
import httpx
import pytest
from openai import APIStatusError, AuthenticationError, BadRequestError
from langchain.tools import tool
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.context_compaction import (
    ContextCompactionError, SUMMARY_MARKER,
)
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
from naiyue_test_agent.model_access.errors import classify_model_failure
from naiyue_test_agent.model_access import ModelCatalog
from test_context_compaction import RoutedFake, _app, _model


class OverflowFake(RoutedFake):
    attempts: list[object]

    def _generate(self, messages, *args, **kwargs):
        if messages and str(messages[0].content).startswith('Summarize completed'):
            return super()._generate(messages, *args, **kwargs)
        self.normal_inputs.append(messages)
        result = self.attempts.pop(0)
        if isinstance(result, Exception):
            raise result
        return ChatResult(generations=[ChatGeneration(message=result)])


def _status(error_type=BadRequestError, *, status=400, code='context_length_exceeded',
            message='PRIVATE_ERROR'):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://example.invalid'))
    return error_type(message, response=response, body={'code': code, 'message': message})


def _configured_model(attempts, summaries=None):
    return OverflowFake(
        responses=[AIMessage(content='unused')], attempts=attempts,
        summary_responses=([AIMessage(content='brief summary')]
                           if summaries is None else summaries),
        metadata=_model([], window=2000, output=20, reserve=20, keep=10).metadata,
    )


def _history():
    messages = [HumanMessage(content='PRIVATE_OLD_' + 'x' * 200),
            AIMessage(content='old answer'),
            HumanMessage(content='PRIVATE_RECENT_' + 'y' * 100),
            AIMessage(content='recent answer'), HumanMessage(content='new question')]
    # Stable IDs and expected provenance retain full-object equality through recovery.
    for index, message in enumerate(messages):
        message.id = f'history-{index}'
        if isinstance(message, HumanMessage):
            message.additional_kwargs['naiyue_human_input'] = True
    return messages


def _events(caplog, attribute):
    return [getattr(record, attribute) for record in caplog.records
            if hasattr(record, attribute)]


@pytest.mark.parametrize('error', [
    _status(),
    _status(code=None, message='Your input exceeds the context window of this model. PRIVATE_INPUT'),
    _status(anthropic.BadRequestError, code=None,
            message='prompt is too long: 210000 tokens > 200000 maximum PRIVATE_INPUT'),
])
@async_test
async def test_explicit_overflow_forces_compaction_below_local_threshold(monkeypatch, caplog, error):
    model = _configured_model([error, AIMessage(content='recovered')])
    app = await _app(monkeypatch, {'A': model})
    session = await app.acreate_session('A')
    messages = _history()
    assert count_tokens_approximately([SystemMessage(content='BASE'), *messages]) < 1980
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = await app.arun(session, messages)

    assert classify_model_failure(error) == ('request_rejected', 'context_window_exceeded')
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1
    assert model.normal_inputs[0][1:] == messages
    retry = model.normal_inputs[1]
    assert retry[1].additional_kwargs[SUMMARY_MARKER] is True
    assert retry[2:] == messages[2:]
    assert count_tokens_approximately(retry) < count_tokens_approximately(model.normal_inputs[0])
    assert result['messages'] == [*retry[1:], result['messages'][-1]]
    assert result['messages'][-1].content == 'recovered'
    thread = app._sessions.require(session).get_thread_id()
    latest = await app._agent.aget_state({'configurable': {'thread_id': thread}})
    assert latest.values['messages'] == result['messages']
    assert app._sessions.require(session).describe_session()['busy'] is False
    recovery = _events(caplog, 'context_recovery')
    assert [event['event'] for event in recovery] == [
        'context_overflow_detected', 'context_overflow_retry_started',
        'context_overflow_retry_returned',
    ]
    assert all(event['session_id'] == session and event['model_id'] == 'A' for event in recovery)
    requests = _events(caplog, 'model_request_context')
    assert [event['outcome'] for event in requests] == ['failed', 'returned']
    assert _events(caplog, 'run_event')[-1]['event'] == 'run_completed'
    assert 'PRIVATE_' not in caplog.text and 'PRIVATE_' not in json.dumps(recovery)


@pytest.mark.parametrize('error', [
    RuntimeError('Your input exceeds the context window of this model'),
    _status(code=None, message='Unsupported max_output_tokens value'),
    _status(APIStatusError, status=429),
    _status(AuthenticationError, status=401),
    _status(anthropic.APIStatusError, status=429, code=None, message='prompt is too long'),
])
@async_test
async def test_other_errors_are_not_retried(monkeypatch, caplog, error):
    model = _configured_model([error])
    app = await _app(monkeypatch, {'A': model})
    session = await app.acreate_session('A')
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(type(error)) as caught:
            await app.arun(session, _history())
    assert caught.value is error
    assert len(model.normal_inputs) == 1 and model.summary_inputs == []
    assert _events(caplog, 'context_recovery') == []
    assert _events(caplog, 'run_event')[-1]['event'] == 'run_failed'


@async_test
async def test_normal_length_response_does_not_trigger_overflow_recovery(monkeypatch):
    model = _configured_model([AIMessage(content='partial', response_metadata={'finish_reason': 'length'})])
    app = await _app(monkeypatch, {'A': model})
    assert (await app.arun(await app.acreate_session('A'), _history()))['messages'][-1].content == 'partial'
    assert len(model.normal_inputs) == 1 and model.summary_inputs == []


@pytest.mark.parametrize('second_error', [_status(), TimeoutError('PRIVATE_RETRY')])
@async_test
async def test_second_model_failure_stops_without_committing_summary(monkeypatch, caplog, second_error):
    model = _configured_model([_status(), second_error])
    app = await _app(monkeypatch, {'A': model})
    session = await app.acreate_session('A')
    messages = _history()
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(type(second_error)) as caught:
            await app.arun(session, messages)
    assert caught.value is second_error
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1
    thread = app._sessions.require(session).get_thread_id()
    assert (await app._agent.aget_state({'configurable': {'thread_id': thread}})).values['messages'] == messages
    assert app._sessions.require(session).describe_session()['busy'] is False
    recovery = _events(caplog, 'context_recovery')
    assert recovery[-1]['event'] == 'context_overflow_recovery_failed'
    assert recovery[-1]['stage'] == 'retry'
    assert _events(caplog, 'run_event')[-1]['event'] == 'run_failed'


@pytest.mark.parametrize('summary_result', [
    TimeoutError('PRIVATE_SUMMARY'), AIMessage(content=''),
    AIMessage(content='partial', response_metadata={'finish_reason': 'length'}),
])
@async_test
async def test_forced_summary_failure_cannot_fall_back_to_rejected_input(monkeypatch, caplog, summary_result):
    model = _configured_model([_status()], summaries=[summary_result])
    app = await _app(monkeypatch, {'A': model})
    session = await app.acreate_session('A')
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(ContextCompactionError):
            await app.arun(session, _history())
    assert len(model.normal_inputs) == len(model.summary_inputs) == 1
    assert not any(event['event'] == 'context_compaction_fallback'
                   for event in _events(caplog, 'context_compaction'))
    assert _events(caplog, 'context_recovery')[-1]['stage'] == 'compact'
    assert app._sessions.require(session).describe_session()['busy'] is False


@pytest.mark.parametrize(('messages', 'summaries', 'reason'), [
    ([HumanMessage(content='only request ' + 'x' * 200)], [], 'no_safe_cut'),
    (_history(), [AIMessage(content='z' * 600)], 'compaction_not_reducing'),
])
@async_test
async def test_forced_compaction_must_find_a_safe_reducing_plan(monkeypatch, messages, summaries, reason):
    model = _configured_model([_status()], summaries=summaries)
    app = await _app(monkeypatch, {'A': model})
    with pytest.raises(ContextCompactionError, match=reason):
        await app.arun(await app.acreate_session('A'), messages)
    assert len(model.normal_inputs) == 1


@async_test
async def test_tools_before_overflow_are_not_replayed_and_checkpoint_continues(monkeypatch):
    executions = []

    @tool
    def effect_read() -> str:
        """Return a controlled test observation."""
        executions.append('executed')
        return 'PRIVATE_TOOL_RESULT'

    model = _configured_model([
        AIMessage(content='', tool_calls=[{'name': 'effect_read', 'args': {}, 'id': 'call-1'}]),
        _status(), AIMessage(content='recovered'), AIMessage(content='next answer'),
    ])
    app = await _app(monkeypatch, {'A': model}, tools=[ToolSpec(effect_read, effect_read.args_schema, permissions=ToolPermission(default='allow'))])
    session = await app.acreate_session('A')
    result = await app.arun(session, _history()[:2] + [HumanMessage(content='execute the tool')])
    assert executions == ['executed']
    assert len(model.normal_inputs) == 3 and len(model.summary_inputs) == 1
    assert [item.type for item in result['messages']] == ['human', 'human', 'ai', 'tool', 'ai']
    assert result['messages'][2].tool_calls[0]['id'] == result['messages'][3].tool_call_id == 'call-1'
    assert result['messages'][-1].content == 'recovered'
    continued = await app.arun(session, [('user', 'next')])
    assert continued['messages'][:len(result['messages'])] == result['messages']
    assert model.normal_inputs[-1][1:-1] == result['messages']
    assert continued['messages'][-1].content == 'next answer'
    assert executions == ['executed']


@async_test
async def test_new_tool_call_returned_by_retry_remains_routable(monkeypatch):
    executions = []

    @tool
    def echo_recovery() -> str:
        """Return a recovery test result."""
        executions.append('executed')
        return 'ok'

    model = _configured_model([
        _status(),
        AIMessage(content='', tool_calls=[{'name': 'echo_recovery', 'args': {}, 'id': 'new-call'}]),
        AIMessage(content='done'),
    ])
    app = await _app(monkeypatch, {'A': model}, tools=[ToolSpec(echo_recovery, echo_recovery.args_schema, permissions=ToolPermission(default='allow'))])
    result = await app.arun(await app.acreate_session('A'), _history())
    assert executions == ['executed']
    assert len(model.normal_inputs) == 3 and len(model.summary_inputs) == 1
    assert result['messages'][-2].tool_call_id == 'new-call'
    assert result['messages'][-1].content == 'done'


@async_test
async def test_retry_uses_frozen_project_instructions(monkeypatch, tmp_path):
    (tmp_path / 'AGENTS.md').write_text('Keep PRIVATE_WORKSPACE_CONSTRAINT.', encoding='utf-8')
    model = _configured_model([_status(), AIMessage(content='recovered')])
    app = await _app(monkeypatch, {'A': model})
    await app.arun(await app.acreate_workspace_session('A', tmp_path), _history())
    assert model.normal_inputs[0][0].content == model.normal_inputs[1][0].content
    assert 'Keep PRIVATE_WORKSPACE_CONSTRAINT.' in model.normal_inputs[1][0].content


@async_test
async def test_rejected_retry_response_does_not_commit_compaction(monkeypatch):
    invalid = AIMessage(content='', invalid_tool_calls=[
        {'name': 'read', 'args': '{', 'id': 'bad', 'error': 'PRIVATE_PARSE'},
    ])
    model = _configured_model([_status(), invalid])
    app = await _app(monkeypatch, {'A': model})
    session = await app.acreate_session('A')
    messages = _history()
    with pytest.raises(ModelResponseRejected, match='invalid_tool_calls'):
        await app.arun(session, messages)
    thread = app._sessions.require(session).get_thread_id()
    assert (await app._agent.aget_state({'configurable': {'thread_id': thread}})).values['messages'] == messages
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1


@async_test
async def test_recovery_keeps_structured_response_and_its_tool_message(monkeypatch):
    class RecoveryResult(BaseModel):
        answer: str

    model = _configured_model([
        _status(), AIMessage(content='', tool_calls=[
            {'name': 'RecoveryResult', 'args': {'answer': 'recovered'}, 'id': 'structured'},
        ]),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        'synthetic.json', tools=[], system_prompt='BASE',
        response_format=ToolStrategy(RecoveryResult),
    )
    result = await app.arun(await app.acreate_session('A'), _history())
    assert result['structured_response'] == RecoveryResult(answer='recovered')
    assert result['messages'][0].additional_kwargs[SUMMARY_MARKER] is True
    assert result['messages'][-2].tool_calls[0]['id'] == 'structured'
    assert result['messages'][-1].tool_call_id == 'structured'
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1


@async_test
async def test_incomplete_original_tool_history_is_rejected_before_forced_summary(monkeypatch):
    model = _configured_model([_status()])
    app = await _app(monkeypatch, {'A': model})
    messages = _history()
    messages[3] = AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {}, 'id': 'unanswered'},
    ])
    with pytest.raises(ContextCompactionError, match='incomplete_tool_pair'):
        await app.arun(await app.acreate_session('A'), messages)
    assert len(model.normal_inputs) == 1 and model.summary_inputs == []
