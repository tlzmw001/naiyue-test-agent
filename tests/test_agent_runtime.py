
from native_application_helpers import async_test


from naiyue_test_agent.application import prepare_tool_registry
import logging

import anthropic
import httpx
from ollama import ResponseError
from openai import AuthenticationError
from openai import APIConnectionError, APITimeoutError
import pytest

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from naiyue_test_agent.agent_runtime import (
    RunContext,
    build_runtime_agent,
    make_model_selection_middleware,
)


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class RecordingFake(FakeMessagesListChatModel):
    seen_inputs: list[list[tuple[str, str]]] = Field(default_factory=list)

    def _generate(self, messages, *args, **kwargs):
        self.seen_inputs.append([(message.type, str(message.content)) for message in messages])
        return super()._generate(messages, *args, **kwargs)


@async_test
async def test_checkpointer_continues_messages_and_switches_model_between_invokes():
    selected = {'s1': 'A', 's2': 'A'}
    models = {
        'A': RecordingFake(responses=[AIMessage(content='answer-A1'), AIMessage(content='answer-A2')]),
        'B': RecordingFake(responses=[AIMessage(content='answer-B')]),
    }
    selections = []
    _runtime_registry_3 = prepare_tool_registry([])
    _runtime_names_3 = _runtime_registry_3.names()
    agent = build_runtime_agent(
        models['A'], tool_registry=_runtime_registry_3, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_3: names, system_prompt='', response_format=None,
        resolve_model=lambda session_id, agent_path='root': (selected[session_id], models[selected[session_id]]),
        record_selection=lambda session_id, model_id: selections.append((session_id, model_id)),
        checkpointer=InMemorySaver(),
    )

    first = (await agent.ainvoke(
        {'messages': [('user', 'first question')]},
        context={'session_id': 's1'}, config={'configurable': {'thread_id': 'thread-s1'}},
    ))
    selected['s1'] = 'B'
    second = (await agent.ainvoke(
        {'messages': [('user', 'follow-up question')]},
        context={'session_id': 's1'}, config={'configurable': {'thread_id': 'thread-s1'}},
    ))
    other = (await agent.ainvoke(
        {'messages': [('user', 'other session question')]},
        context={'session_id': 's2'}, config={'configurable': {'thread_id': 'thread-s2'}},
    ))

    assert [message.content for message in first['messages']] == ['first question', 'answer-A1']
    assert [message.content for message in second['messages']] == [
        'first question', 'answer-A1', 'follow-up question', 'answer-B',
    ]
    assert models['B'].seen_inputs == [[
        ('system', ''), ('human', 'first question'), ('ai', 'answer-A1'),
        ('human', 'follow-up question'),
    ]]
    assert [message.content for message in other['messages']] == [
        'other session question', 'answer-A2',
    ]
    assert models['A'].seen_inputs[-1] == [
        ('system', ''), ('human', 'other session question'),
    ]
    assert selections == [('s1', 'A'), ('s1', 'B'), ('s2', 'A')]


@async_test
async def test_switches_model_after_tool_result_in_same_run():
    selected = {'s1': 'A'}
    models = {
        'A': ToolCapableFake(responses=[AIMessage(
            content='', tool_calls=[{'name': 'switch_to_b', 'args': {}, 'id': 'call-1'}]
        )]),
        'B': ToolCapableFake(responses=[AIMessage(content='answer-from-B')]),
    }
    selections = []

    @tool
    def switch_to_b() -> str:
        """Change this test session's selected model to B."""
        selected['s1'] = 'B'
        return 'selected B'

    def resolve_model(session_id, agent_path='root'):
        model_id = selected[session_id]
        return model_id, models[model_id]

    agent = create_agent(
        model=models['A'], tools=[switch_to_b], context_schema=RunContext,
        middleware=[make_model_selection_middleware(
            resolve_model, lambda session_id, model_id: selections.append((session_id, model_id))
        )],
    )
    result = (await agent.ainvoke(
        {'messages': [('user', 'switch model, then answer')]},
        context={'session_id': 's1'},
    ))

    assert selections == [('s1', 'A'), ('s1', 'B')]
    assert [message.type for message in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert result['messages'][2].tool_call_id == result['messages'][1].tool_calls[0]['id']
    assert result['messages'][-1].content == 'answer-from-B'


@async_test
async def test_model_selection_is_isolated_by_session(caplog):
    selected = {'s1': 'A', 's2': 'B'}
    models = {
        'A': FakeListChatModel(responses=['answer-from-A']),
        'B': FakeListChatModel(responses=['answer-from-B']),
    }
    selections = []

    def resolve_model(session_id, agent_path='root'):
        model_id = selected[session_id]
        return model_id, models[model_id]

    agent = create_agent(
        model=models['A'], tools=[], context_schema=RunContext,
        middleware=[make_model_selection_middleware(
            resolve_model, lambda session_id, model_id: selections.append((session_id, model_id))
        )],
    )
    answer_s1 = (await agent.ainvoke({'messages': [('user', 'ping')]}, context={'session_id': 's1'}))
    answer_s2 = (await agent.ainvoke({'messages': [('user', 'ping')]}, context={'session_id': 's2'}))

    assert answer_s1['messages'][-1].content == 'answer-from-A'
    assert answer_s2['messages'][-1].content == 'answer-from-B'
    assert selections == [('s1', 'A'), ('s2', 'B')]
    assert not any(hasattr(record, 'model_failure') for record in caplog.records)


@async_test
@pytest.mark.parametrize(('usage', 'expected_input', 'expected_cached'), [
    ({'input_tokens': 1500, 'output_tokens': 5, 'total_tokens': 1505,
      'input_token_details': {'cache_read': 1024}}, 1500, 1024),
    ({'input_tokens': 1500, 'output_tokens': 5, 'total_tokens': 1505,
      'input_token_details': {'cache_read': 0}}, 1500, 0),
    ({'input_tokens': 1500, 'output_tokens': 5, 'total_tokens': 1505}, 1500, None),
    (None, None, None),
])
async def test_successful_model_call_records_reported_cache_usage(
    usage, expected_input, expected_cached, caplog,
):
    model = FakeMessagesListChatModel(responses=[AIMessage(
        content='unchanged answer', usage_metadata=usage,
    )])
    _runtime_registry_4 = prepare_tool_registry([])
    _runtime_names_4 = _runtime_registry_4.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_4, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_4: names, system_prompt='', response_format=None,
        resolve_model=lambda _session_id, agent_path='root': ('model-A', model),
        record_selection=lambda _session_id, _model_id: None,
    )

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = (await agent.ainvoke({'messages': [('user', 'ping')]}, context={'session_id': 's1'}))

    events = [record.model_usage for record in caplog.records
              if hasattr(record, 'model_usage')]
    assert events == [{
        'event': 'model_call_usage', 'model_id': 'model-A',
        'input_tokens': expected_input, 'cache_read_tokens': expected_cached,
    }]
    assert result['messages'][-1].content == 'unchanged answer'
    assert 'unchanged answer' not in caplog.text
    assert 'ping' not in caplog.text


@async_test
async def test_missing_session_id_fails_before_model_call(caplog):
    calls = []
    model = FakeListChatModel(responses=['unexpected'])

    def resolve_model(session_id, agent_path='root'):
        calls.append(session_id)
        return 'A', model

    agent = create_agent(
        model=model, tools=[], context_schema=RunContext,
        middleware=[make_model_selection_middleware(resolve_model, lambda _session_id, _model_id: None)],
    )

    with pytest.raises(ValueError, match='session_id'):
        (await agent.ainvoke({'messages': [('user', 'ping')]}, context={}))
    assert calls == []
    assert not any(hasattr(record, 'model_failure') for record in caplog.records)


@async_test
async def test_selection_recorder_failure_is_not_a_model_call_failure(caplog):
    model = FakeListChatModel(responses=['unexpected'])

    def fail_recording(_session_id, _model_id):
        raise RuntimeError('selection recorder failed')

    agent = create_agent(
        model=model, tools=[], context_schema=RunContext,
        middleware=[make_model_selection_middleware(
            lambda _session_id, agent_path='root': ('model-A', model), fail_recording,
        )],
    )

    with pytest.raises(RuntimeError, match='selection recorder failed'):
        (await agent.ainvoke({'messages': [('user', 'ping')]}, context={'session_id': 's1'}))
    assert not any(hasattr(record, 'model_failure') for record in caplog.records)


@async_test
@pytest.mark.parametrize(('error_type', 'category', 'reason'), [
    (TimeoutError, 'transient', 'timeout'),
    (RuntimeError, 'unknown', 'unclassified'),
])
async def test_model_failure_records_safe_event_and_propagates_same_exception(
    error_type, category, reason, caplog,
):
    failure = error_type('secret-marker in provider error')

    class FailingModel(FakeListChatModel):
        async def _agenerate(self, *args, **kwargs):
            raise failure

    model = FailingModel(responses=['unused'])
    selections = []
    _runtime_registry_5 = prepare_tool_registry([])
    _runtime_names_5 = _runtime_registry_5.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_5, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_5: names, system_prompt='', response_format=None,
        resolve_model=lambda session_id, agent_path='root': ('model-A', model),
        record_selection=lambda session_id, model_id: selections.append((session_id, model_id)),
    )

    with caplog.at_level(logging.ERROR, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(error_type) as caught:
            (await agent.ainvoke({'messages': [('user', 'ping')]}, context={'session_id': 's1'}))

    assert caught.value is failure
    assert selections == [('s1', 'model-A')]
    events = [record.model_failure for record in caplog.records
              if hasattr(record, 'model_failure')]
    expected_event = {
        'event': 'model_call_failed', 'model_id': 'model-A',
        'category': category, 'reason': reason,
    }
    if category == 'unknown':
        expected_event['error_type'] = error_type.__name__
    assert events == [expected_event]
    assert 'secret-marker' not in caplog.text
    assert not any(hasattr(record, 'model_usage') for record in caplog.records)


@async_test
async def test_openai_authentication_failure_is_classified_without_leaking_response(caplog):
    request = httpx.Request('POST', 'https://example.invalid/responses')
    response = httpx.Response(
        401, request=request, json={'error': {'message': 'secret-marker in response'}},
    )
    failure = AuthenticationError(
        'secret-marker in provider error', response=response, body=response.json(),
    )

    class FailingModel(FakeListChatModel):
        async def _agenerate(self, *args, **kwargs):
            raise failure

    model = FailingModel(responses=['unused'])
    _runtime_registry_6 = prepare_tool_registry([])
    _runtime_names_6 = _runtime_registry_6.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_6, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_6: names, system_prompt='', response_format=None,
        resolve_model=lambda _session_id, agent_path='root': ('model-A', model),
        record_selection=lambda _session_id, _model_id: None,
    )

    with caplog.at_level(logging.ERROR, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(AuthenticationError) as caught:
            (await agent.ainvoke({'messages': [('user', 'ping')]}, context={'session_id': 's1'}))

    assert caught.value is failure
    events = [record.model_failure for record in caplog.records
              if hasattr(record, 'model_failure')]
    assert events == [{
        'event': 'model_call_failed', 'model_id': 'model-A',
        'category': 'access_denied', 'reason': 'authentication_failed',
    }]
    assert 'secret-marker' not in caplog.text
    assert not any(hasattr(record, 'model_usage') for record in caplog.records)


@async_test
@pytest.mark.parametrize(('error_type', 'reason'), [
    (APITimeoutError, 'timeout'),
    (APIConnectionError, 'connection_error'),
])
async def test_openai_transport_failure_reaches_agent_as_terminal_error(error_type, reason, caplog):
    failure = error_type(request=httpx.Request('POST', 'https://example.invalid/responses'))

    class FailingModel(FakeListChatModel):
        async def _agenerate(self, *args, **kwargs):
            raise failure

    model = FailingModel(responses=['unused'])
    _runtime_registry_7 = prepare_tool_registry([])
    _runtime_names_7 = _runtime_registry_7.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_7, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_7: names, system_prompt='', response_format=None,
        resolve_model=lambda _session_id, agent_path='root': ('model-A', model),
        record_selection=lambda _session_id, _model_id: None,
    )

    with caplog.at_level(logging.ERROR, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(error_type) as caught:
            (await agent.ainvoke({'messages': [('user', 'secret-marker')]}, context={'session_id': 's1'}))

    assert caught.value is failure
    events = [record.model_failure for record in caplog.records
              if hasattr(record, 'model_failure')]
    assert events == [{
        'event': 'model_call_failed', 'model_id': 'model-A',
        'category': 'transient', 'reason': reason,
    }]
    assert 'secret-marker' not in caplog.text


@async_test
@pytest.mark.parametrize(('failure', 'category', 'reason'), [
    (anthropic.AuthenticationError(
        'secret-marker in provider error',
        response=httpx.Response(
            401, request=httpx.Request('POST', 'https://example.invalid/messages')),
        body=None,
    ), 'access_denied', 'authentication_failed'),
    (ResponseError('secret-marker in provider error', 404),
     'request_rejected', 'not_found'),
])
async def test_non_openai_failure_reaches_agent_event_without_leaking_body(
    failure, category, reason, caplog,
):
    class FailingModel(FakeListChatModel):
        async def _agenerate(self, *args, **kwargs):
            raise failure

    model = FailingModel(responses=['unused'])
    _runtime_registry_8 = prepare_tool_registry([])
    _runtime_names_8 = _runtime_registry_8.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_8, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_8: names, system_prompt='', response_format=None,
        resolve_model=lambda _session_id, agent_path='root': ('model-A', model),
        record_selection=lambda _session_id, _model_id: None,
    )

    with caplog.at_level(logging.ERROR, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(type(failure)) as caught:
            (await agent.ainvoke({'messages': [('user', 'ping')]}, context={'session_id': 's1'}))

    assert caught.value is failure
    events = [record.model_failure for record in caplog.records
              if hasattr(record, 'model_failure')]
    assert events == [{
        'event': 'model_call_failed', 'model_id': 'model-A',
        'category': category, 'reason': reason,
    }]
    assert 'secret-marker' not in caplog.text
    assert not any(hasattr(record, 'model_usage') for record in caplog.records)
