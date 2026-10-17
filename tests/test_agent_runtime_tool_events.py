"""Tool-boundary events through the project's asynchronous LangChain agent."""

from native_application_helpers import async_test

from naiyue_test_agent.agent_runtime.async_invocation import ainvoke_agent_run

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

from naiyue_test_agent.application import prepare_tool_registry

import logging

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.agent_runtime import build_runtime_agent


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def build_fake_agent(spec, responses):
    model = ToolCapableFake(responses=responses)
    _runtime_registry_1 = prepare_tool_registry([spec])
    _runtime_names_1 = _runtime_registry_1.names()
    return build_runtime_agent(
        model, tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
        resolve_model=lambda _session_id, agent_path='root': ('fake-model', model),
        record_selection=lambda _session_id, _model_id: None,
    )


@async_test
async def test_tool_success_has_correlated_safe_events_in_actual_agent_order(caplog):
    @tool
    def read_probe(value: str) -> str:
        """Read a synthetic probe value."""
        assert value == 'SENSITIVE_ARG'
        return 'SENSITIVE_TOOL_OUTPUT'

    agent = build_fake_agent(ToolSpec(read_probe, read_probe.args_schema, permissions=ToolPermission(default='allow')), [
        AIMessage(content='', tool_calls=[{
            'name': 'read_probe', 'args': {'value': 'SENSITIVE_ARG'}, 'id': 'call-1',
        }]),
        AIMessage(content='SENSITIVE_FINAL'),
    ])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = (await ainvoke_agent_run(
            agent, [('user', 'SENSITIVE_PROMPT')],
            session_id='session-1', thread_id='INTERNAL_THREAD', run_id='run-1',
        ))

    assert result['messages'][-1].content == 'SENSITIVE_FINAL'
    events = []
    for record in caplog.records:
        for attribute in ('run_event', 'model_selection', 'model_usage', 'tool_event'):
            if hasattr(record, attribute):
                events.append(getattr(record, attribute))
    assert [event['event'] for event in events] == [
        'run_started', 'model_selected', 'model_call_usage',
        'tool_started', 'tool_completed',
        'model_selected', 'model_call_usage', 'run_completed',
    ]
    assert [event for event in events if event['event'].startswith('run_')] == [
        {'event': 'run_started', 'run_id': 'run-1', 'session_id': 'session-1'},
        {'event': 'run_completed', 'run_id': 'run-1', 'session_id': 'session-1'},
    ]
    assert [event for event in events if event['event'].startswith('tool_')] == [
        {'event': 'tool_started', 'tool_name': 'read_probe',
         'tool_call_id': 'call-1', 'run_id': 'run-1'},
        {'event': 'tool_completed', 'tool_name': 'read_probe',
         'tool_call_id': 'call-1', 'run_id': 'run-1'},
    ]
    serialized = repr(events) + caplog.text
    for secret in ('SENSITIVE_PROMPT', 'SENSITIVE_ARG', 'SENSITIVE_TOOL_OUTPUT',
                   'SENSITIVE_FINAL', 'INTERNAL_THREAD'):
        assert secret not in serialized


@async_test
async def test_ordinary_tool_exception_records_failure_and_returns_error_result(caplog):
    failure = RuntimeError('SENSITIVE_ERROR')

    @tool
    def crash_probe() -> str:
        """Raise a synthetic failure."""
        raise failure

    agent = build_fake_agent(ToolSpec(crash_probe, crash_probe.args_schema, permissions=ToolPermission(default='allow')), [AIMessage(content='', tool_calls=[
        {'name': 'crash_probe', 'args': {}, 'id': 'call-2'},
    ]), AIMessage(content='handled')])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = (await ainvoke_agent_run(
            agent, [('user', 'SENSITIVE_PROMPT')],
            session_id='session-2', thread_id='INTERNAL_THREAD', run_id='run-2',
        ))

    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert feedback.status == 'error' and feedback.tool_call_id == 'call-2'
    assert 'RuntimeError' in feedback.content and 'SENSITIVE_ERROR' not in feedback.content
    assert result['messages'][-1].content == 'handled'
    tool_events = [r.tool_event for r in caplog.records if hasattr(r, 'tool_event')]
    assert tool_events == [
        {'event': 'tool_started', 'tool_name': 'crash_probe',
         'tool_call_id': 'call-2', 'run_id': 'run-2'},
        {'event': 'tool_failed', 'tool_name': 'crash_probe',
         'tool_call_id': 'call-2', 'run_id': 'run-2'},
    ]
    assert [r.run_event for r in caplog.records if hasattr(r, 'run_event')] == [
        {'event': 'run_started', 'run_id': 'run-2', 'session_id': 'session-2'},
        {'event': 'run_completed', 'run_id': 'run-2', 'session_id': 'session-2'},
    ]
    assert 'SENSITIVE_ERROR' not in repr(tool_events) + caplog.text
    assert 'INTERNAL_THREAD' not in repr(tool_events) + caplog.text


@async_test
async def test_error_tool_message_is_failure_event_but_run_can_complete(caplog):
    @tool
    def recoverable_probe() -> str:
        """Return a handled synthetic tool error."""
        raise ToolException('SENSITIVE_HANDLED_ERROR')

    agent = build_fake_agent(ToolSpec(recoverable_probe, recoverable_probe.args_schema, permissions=ToolPermission(default='allow')), [
        AIMessage(content='', tool_calls=[
            {'name': 'recoverable_probe', 'args': {}, 'id': 'call-3'},
        ]),
        AIMessage(content='final-after-error'),
    ])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = (await ainvoke_agent_run(
            agent, [('user', 'probe')],
            session_id='session-3', thread_id='INTERNAL_THREAD', run_id='run-3',
        ))

    tool_messages = [m for m in result['messages'] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].status == 'error'
    assert result['messages'][-1].content == 'final-after-error'
    assert [r.tool_event['event'] for r in caplog.records if hasattr(r, 'tool_event')] == [
        'tool_started', 'tool_failed',
    ]
    assert [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')] == [
        'run_started', 'run_completed',
    ]
    assert 'SENSITIVE_HANDLED_ERROR' not in caplog.text


@async_test
async def test_direct_invoke_does_not_invent_run_id_for_tool_events(caplog):
    @tool
    def plain_probe() -> str:
        """Return a synthetic value."""
        return 'value'

    agent = build_fake_agent(ToolSpec(plain_probe, plain_probe.args_schema, permissions=ToolPermission(default='allow')), [
        AIMessage(content='', tool_calls=[
            {'name': 'plain_probe', 'args': {}, 'id': 'call-4'},
        ]),
        AIMessage(content='final'),
    ])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        (await agent.ainvoke({'messages': [('user', 'probe')]}, context={'session_id': 'session-4'}))

    assert [r.tool_event for r in caplog.records if hasattr(r, 'tool_event')] == [
        {'event': 'tool_started', 'tool_name': 'plain_probe', 'tool_call_id': 'call-4'},
        {'event': 'tool_completed', 'tool_name': 'plain_probe', 'tool_call_id': 'call-4'},
    ]


@async_test
async def test_async_runs_emit_correlated_start_model_and_terminal_events(caplog):
    model = FakeMessagesListChatModel(responses=[
        AIMessage(content='answer-one'), AIMessage(content='answer-two'),
    ])
    _runtime_registry_1 = prepare_tool_registry([])
    _runtime_names_1 = _runtime_registry_1.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
        resolve_model=lambda _session_id, agent_path='root': ('model-A', model),
        record_selection=lambda _session_id, _model_id: None,
        checkpointer=InMemorySaver(),
    )

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        first = (await ainvoke_agent_run(
            agent, [('user', 'private-question-one')],
            session_id='s1', thread_id='internal-s1', run_id='run-one',
        ))
        second = (await ainvoke_agent_run(
            agent, [('user', 'private-question-two')],
            session_id='s1', thread_id='internal-s1', run_id='run-two',
        ))

    run_events = [record.run_event for record in caplog.records if hasattr(record, 'run_event')]
    assert run_events == [
        {'event': 'run_started', 'run_id': 'run-one', 'session_id': 's1'},
        {'event': 'run_completed', 'run_id': 'run-one', 'session_id': 's1'},
        {'event': 'run_started', 'run_id': 'run-two', 'session_id': 's1'},
        {'event': 'run_completed', 'run_id': 'run-two', 'session_id': 's1'},
    ]
    model_selections = [record.model_selection for record in caplog.records
                        if hasattr(record, 'model_selection')]
    assert model_selections == [
        {'event': 'model_selected', 'run_id': 'run-one', 'model_id': 'model-A'},
        {'event': 'model_selected', 'run_id': 'run-two', 'model_id': 'model-A'},
    ]
    model_usages = [record.model_usage for record in caplog.records
                    if hasattr(record, 'model_usage')]
    assert [usage['run_id'] for usage in model_usages] == ['run-one', 'run-two']
    ordered_events = []
    for record in caplog.records:
        for attribute in ('run_event', 'model_selection', 'model_usage'):
            if hasattr(record, attribute):
                payload = getattr(record, attribute)
                ordered_events.append((payload['event'], payload['run_id']))
    assert ordered_events == [
        ('run_started', 'run-one'), ('model_selected', 'run-one'),
        ('model_call_usage', 'run-one'), ('run_completed', 'run-one'),
        ('run_started', 'run-two'), ('model_selected', 'run-two'),
        ('model_call_usage', 'run-two'), ('run_completed', 'run-two'),
    ]
    assert [message.content for message in first['messages']] == ['private-question-one', 'answer-one']
    assert [message.content for message in second['messages']] == [
        'private-question-one', 'answer-one', 'private-question-two', 'answer-two',
    ]
    assert 'private-question-one' not in caplog.text
    assert 'private-question-two' not in caplog.text
    assert 'answer-one' not in caplog.text
    assert 'internal-s1' not in caplog.text


@async_test
async def test_async_run_failure_emits_terminal_event_and_rethrows_same_error(caplog):
    failure = RuntimeError('private-provider-error')

    class FailingModel(FakeListChatModel):
        async def _agenerate(self, *args, **kwargs):
            raise failure

    model = FailingModel(responses=['unused'])
    _runtime_registry_2 = prepare_tool_registry([])
    _runtime_names_2 = _runtime_registry_2.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_2, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_2: names, system_prompt='', response_format=None,
        resolve_model=lambda _session_id, agent_path='root': ('model-A', model),
        record_selection=lambda _session_id, _model_id: None,
    )

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(RuntimeError) as caught:
            (await ainvoke_agent_run(
                agent, [('user', 'private-question')],
                session_id='s1', thread_id='internal-s1', run_id='run-failed',
            ))

    assert caught.value is failure
    assert [record.run_event for record in caplog.records if hasattr(record, 'run_event')] == [
        {'event': 'run_started', 'run_id': 'run-failed', 'session_id': 's1'},
        {'event': 'run_failed', 'run_id': 'run-failed', 'session_id': 's1'},
    ]
    assert [record.model_failure for record in caplog.records
            if hasattr(record, 'model_failure')] == [{
        'event': 'model_call_failed', 'run_id': 'run-failed', 'model_id': 'model-A',
        'category': 'unknown', 'reason': 'unclassified', 'error_type': 'RuntimeError',
    }]
    assert not any(hasattr(record, 'model_usage') for record in caplog.records)
    assert 'private-provider-error' not in caplog.text
    assert 'private-question' not in caplog.text
    assert 'internal-s1' not in caplog.text


@async_test
@pytest.mark.parametrize(('session_id', 'thread_id', 'run_id'), [
    ('', 'thread', 'run'), ('session', '', 'run'), ('session', 'thread', ''),
])
async def test_async_run_rejects_missing_identifiers_before_invocation(
    session_id, thread_id, run_id, caplog,
):
    class UnexpectedAgent:
        async def ainvoke(self, *_args, **_kwargs):
            raise AssertionError('agent must not be invoked')

    with pytest.raises(ValueError):
        (await ainvoke_agent_run(
            UnexpectedAgent(), [('user', 'private-question')],
            session_id=session_id, thread_id=thread_id, run_id=run_id,
        ))
    assert not any(hasattr(record, 'run_event') for record in caplog.records)
