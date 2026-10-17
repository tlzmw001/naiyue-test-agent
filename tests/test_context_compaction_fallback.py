"""Failed proactive summaries may fall back only with usable original history."""

from native_application_helpers import async_test, build_application

import logging
from concurrent.futures import CancelledError

import anthropic
import httpx
import pytest
from ollama import ResponseError
from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult

from naiyue_test_agent import application
from test_application_run_events import ToolModel
from naiyue_test_agent.agent_runtime.context_compaction import (
    ContextCompactionError, SUMMARY_MARKER,
)
from naiyue_test_agent.model_access import ModelCatalog, ModelConfigError
from naiyue_test_agent.model_access.errors import classify_model_failure


class FallbackFake(ToolModel):
    summary_results: list[object]
    summary_inputs: list = []
    normal_inputs: list = []
    normal_failure: object | None = None

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        if messages[0].content.startswith('Summarize completed'):
            self.summary_inputs.append(messages)
            response = self.summary_results.pop(0)
            if isinstance(response, Exception):
                raise response
            return ChatResult(generations=[ChatGeneration(message=response)])
        self.normal_inputs.append(messages)
        if isinstance(self.normal_failure, Exception):
            raise self.normal_failure
        return super()._generate(messages, *args, **kwargs)


def _sdk_status(error_type, status):
    response = httpx.Response(status, request=httpx.Request(
        'POST', 'https://example.invalid/messages',
    ))
    return error_type('PRIVATE_SDK_FAILURE', response=response, body=None)


async def _case(monkeypatch, summary_result, *, send_margin=None, normal_failure=None):
    messages = [
        HumanMessage(content='PRIVATE_FIRST_' + 'x' * 100),
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': 'a.txt'}, 'id': 'call-1'},
        ]),
        ToolMessage(content='PRIVATE_RESULT_' + 'r' * 100, tool_call_id='call-1'),
        AIMessage(content='first done'),
        HumanMessage(content='PRIVATE_SECOND_' + 'y' * 100),
        AIMessage(content='second done'), HumanMessage(content='new question'),
    ]
    # Expected history now includes the application provenance contract; compare every field.
    for index, message in enumerate(messages):
        message.id = f'history-{index}'
        if isinstance(message, HumanMessage):
            message.additional_kwargs['naiyue_human_input'] = True
    estimate = count_tokens_approximately([SystemMessage(content='BASE'), *messages])
    output = 20 if send_margin is None else 400 - (estimate + send_margin)
    model = FallbackFake(
        responses=[AIMessage(content='continued')],
        summary_results=[summary_result], normal_failure=normal_failure,
        metadata={
            'naiyue_model_limits': {'model_id': 'A', 'context_window_tokens': 400,
                                   'max_output_tokens': output},
            'naiyue_compaction_reserve': {'tokens': 350},
            'naiyue_compaction_keep_recent': {'tokens': 10},
            'naiyue_compaction_summary_output': {'tokens': 8},
        },
    )
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        'synthetic.json', tools=[], system_prompt='BASE', response_format=None,
    )
    return app, await app.acreate_session('A'), model, messages, estimate, 400 - output


def _events(caplog, attribute):
    return [getattr(record, attribute) for record in caplog.records
            if hasattr(record, attribute)]


@pytest.mark.parametrize(('summary_result', 'fallback_reason'), [
    (TimeoutError('PRIVATE_TIMEOUT'), 'summary_timeout'),
    (ConnectionError('PRIVATE_CONNECTION'), 'summary_connection_error'),
    (APITimeoutError(httpx.Request('POST', 'https://example.invalid')),
     'summary_timeout'),
    (APIConnectionError(request=httpx.Request('POST', 'https://example.invalid')),
     'summary_connection_error'),
    (_sdk_status(APIStatusError, 503), 'summary_server_error'),
    (anthropic.APITimeoutError(httpx.Request('POST', 'https://example.invalid')),
     'summary_timeout'),
    (_sdk_status(anthropic.APIStatusError, 529), 'summary_server_error'),
    (ResponseError('PRIVATE_OLLAMA_ERROR', 503), 'summary_server_error'),
    (AIMessage(content=''), 'summary_empty'),
    (AIMessage(content='partial', response_metadata={'finish_reason': 'length'}),
     'summary_truncated'),
])
@async_test
async def test_recoverable_summary_failure_preserves_history_and_completes_run(
    monkeypatch, caplog, summary_result, fallback_reason,
):
    app, session, model, messages, estimate, budget = await _case(monkeypatch, summary_result)
    assert 50 < estimate < budget
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = await app.arun(session, messages)

    assert len(model.summary_inputs) == 1
    assert len(model.normal_inputs) == 1
    assert model.normal_inputs[0][1:] == messages
    assert result['messages'][:-1] == messages
    assert result['messages'][-1].content == 'continued'
    assert not any(message.additional_kwargs.get(SUMMARY_MARKER)
                   for message in result['messages'])
    assert app._sessions._sessions[session]['busy'] is False
    thread = app._sessions._sessions[session]['thread_id']
    latest = await app._agent.aget_state({'configurable': {'thread_id': thread}})
    assert latest.values['messages'] == result['messages']
    events = _events(caplog, 'context_compaction')
    names = [event['event'] for event in events]
    assert names.index('context_compaction_failed') < names.index('context_compaction_fallback')
    fallback = next(event for event in events if event['event'] == 'context_compaction_fallback')
    assert fallback['reason'] == fallback_reason
    assert fallback['estimated_input_tokens'] == estimate
    assert fallback['input_budget_tokens'] == budget
    assert fallback['session_id'] == session and fallback['model_id'] == 'A'
    runs = _events(caplog, 'run_event')
    assert [event['event'] for event in runs] == ['run_started', 'run_completed']
    assert fallback['run_id'] == runs[0]['run_id']
    assert 'context_compaction_completed' not in names
    assert 'PRIVATE_' not in caplog.text


@pytest.mark.parametrize(('summary_result', 'reason'), [
    (TimeoutError('PRIVATE_TIMEOUT'), 'summary_failed'),
    (AIMessage(content=''), 'summary_invalid'),
    (AIMessage(content='partial', response_metadata={'finish_reason': 'length'}),
     'summary_invalid'),
])
@pytest.mark.parametrize('send_margin', [0, -1])
@async_test
async def test_original_send_budget_boundary_controls_fallback(
    monkeypatch, caplog, summary_result, reason, send_margin,
):
    app, session, model, messages, estimate, budget = await _case(
        monkeypatch, summary_result, send_margin=send_margin,
    )
    assert budget == estimate + send_margin
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        if send_margin == 0:
            assert (await app.arun(session, messages))['messages'][-1].content == 'continued'
        else:
            with pytest.raises(ContextCompactionError, match=reason):
                await app.arun(session, messages)
    assert len(model.summary_inputs) == 1
    assert len(model.normal_inputs) == (1 if send_margin == 0 else 0)
    events = _events(caplog, 'context_compaction')
    assert any(event['event'] == 'context_compaction_fallback' for event in events) is (send_margin == 0)
    assert _events(caplog, 'run_event')[-1]['event'] == (
        'run_completed' if send_margin == 0 else 'run_failed'
    )
    assert app._sessions._sessions[session]['busy'] is False


@pytest.mark.parametrize('summary_result', [
    RuntimeError('PRIVATE_UNKNOWN'), ValueError('PRIVATE_INTERNAL'),
    ModelConfigError('PRIVATE_CONFIG'), CancelledError('PRIVATE_CANCEL'),
    _sdk_status(AuthenticationError, 401),
    _sdk_status(anthropic.PermissionDeniedError, 403),
    _sdk_status(APIStatusError, 400), _sdk_status(APIStatusError, 429),
    AIMessage(content='', response_metadata={'finish_reason': 'content_filter'}),
    AIMessage(content=[{'type': 'text', 'text': 'non-string structure'}]),
    AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {}, 'id': 'unexpected-call'},
    ], response_metadata={'finish_reason': 'length'}),
    AIMessage(content='', invalid_tool_calls=[
        {'name': 'read', 'args': '{', 'id': 'invalid-call', 'error': 'PRIVATE_PARSE'},
    ]),
])
@async_test
async def test_nonrecoverable_summary_failure_never_falls_back(
    monkeypatch, caplog, summary_result,
):
    app, session, model, messages, estimate, budget = await _case(monkeypatch, summary_result)
    assert estimate < budget
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(ContextCompactionError):
            await app.arun(session, messages)
    assert len(model.summary_inputs) == 1
    assert model.normal_inputs == []
    assert app._sessions._sessions[session]['busy'] is False
    assert not any(event['event'] == 'context_compaction_fallback'
                   for event in _events(caplog, 'context_compaction'))
    assert _events(caplog, 'run_event')[-1]['event'] == 'run_failed'
    assert 'PRIVATE_' not in caplog.text


@async_test
async def test_normal_model_failure_after_fallback_is_still_a_failed_run(monkeypatch, caplog):
    normal_failure = TimeoutError('PRIVATE_NORMAL_FAILURE')
    app, session, model, messages, _, _ = await _case(
        monkeypatch, TimeoutError('PRIVATE_SUMMARY_FAILURE'), normal_failure=normal_failure,
    )
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(TimeoutError) as caught:
            await app.arun(session, messages)
    assert caught.value is normal_failure
    assert len(model.summary_inputs) == len(model.normal_inputs) == 1
    assert model.normal_inputs[0][1:] == messages
    assert [event['event'] for event in _events(caplog, 'run_event')] == [
        'run_started', 'run_failed',
    ]
    assert app._sessions._sessions[session]['busy'] is False
    assert 'PRIVATE_' not in caplog.text


@async_test
async def test_incomplete_original_tool_pair_prevents_fallback(monkeypatch, caplog):
    app, session, model, messages, _, _ = await _case(monkeypatch, TimeoutError())
    messages[5] = AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': 'b.txt'}, 'id': 'unanswered'},
    ])
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(ContextCompactionError, match='summary_failed'):
            await app.arun(session, messages)
    assert len(model.summary_inputs) == 1
    assert model.normal_inputs == []
    assert not any(event['event'] == 'context_compaction_fallback'
                   for event in _events(caplog, 'context_compaction'))
    assert any(event['event'] == 'context_compaction_fallback_rejected'
               and event['reason'] == 'incomplete_tool_pair'
               for event in _events(caplog, 'context_compaction'))


@pytest.mark.parametrize(('status', 'expected'), [
    (500, ('transient', 'server_error')), (503, ('transient', 'server_error')),
    (400, ('unknown', 'unclassified')), (429, ('unknown', 'unclassified')),
])
def test_openai_sdk_status_classification_for_fallback(status, expected):
    assert classify_model_failure(_sdk_status(APIStatusError, status)) == expected
