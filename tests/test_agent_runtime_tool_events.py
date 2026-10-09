"""Tool-boundary events through the project's synchronous LangChain agent."""

import logging

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.agent_runtime import build_runtime_agent, invoke_agent_run


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def build_fake_agent(tool_to_call, responses):
    model = ToolCapableFake(responses=responses)
    return build_runtime_agent(
        model, tools=[tool_to_call], system_prompt='', response_format=None,
        resolve_model=lambda _session_id: ('fake-model', model),
        record_selection=lambda _session_id, _model_id: None,
    )


def test_tool_success_has_correlated_safe_events_in_actual_agent_order(caplog):
    @tool
    def read_probe(value: str) -> str:
        """Read a synthetic probe value."""
        assert value == 'SENSITIVE_ARG'
        return 'SENSITIVE_TOOL_OUTPUT'

    agent = build_fake_agent(read_probe, [
        AIMessage(content='', tool_calls=[{
            'name': 'read_probe', 'args': {'value': 'SENSITIVE_ARG'}, 'id': 'call-1',
        }]),
        AIMessage(content='SENSITIVE_FINAL'),
    ])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = invoke_agent_run(
            agent, [('user', 'SENSITIVE_PROMPT')],
            session_id='session-1', thread_id='INTERNAL_THREAD', run_id='run-1',
        )

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


def test_tool_exception_records_failure_and_propagates_original_error(caplog):
    failure = RuntimeError('SENSITIVE_ERROR')

    @tool
    def crash_probe() -> str:
        """Raise a synthetic failure."""
        raise failure

    agent = build_fake_agent(crash_probe, [AIMessage(content='', tool_calls=[
        {'name': 'crash_probe', 'args': {}, 'id': 'call-2'},
    ])])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(RuntimeError) as caught:
            invoke_agent_run(
                agent, [('user', 'SENSITIVE_PROMPT')],
                session_id='session-2', thread_id='INTERNAL_THREAD', run_id='run-2',
            )

    assert caught.value is failure
    tool_events = [r.tool_event for r in caplog.records if hasattr(r, 'tool_event')]
    assert tool_events == [
        {'event': 'tool_started', 'tool_name': 'crash_probe',
         'tool_call_id': 'call-2', 'run_id': 'run-2'},
        {'event': 'tool_failed', 'tool_name': 'crash_probe',
         'tool_call_id': 'call-2', 'run_id': 'run-2'},
    ]
    assert [r.run_event for r in caplog.records if hasattr(r, 'run_event')] == [
        {'event': 'run_started', 'run_id': 'run-2', 'session_id': 'session-2'},
        {'event': 'run_failed', 'run_id': 'run-2', 'session_id': 'session-2'},
    ]
    assert 'SENSITIVE_ERROR' not in repr(tool_events) + caplog.text
    assert 'INTERNAL_THREAD' not in repr(tool_events) + caplog.text


def test_error_tool_message_is_failure_event_but_run_can_complete(caplog):
    @tool
    def recoverable_probe() -> str:
        """Return a handled synthetic tool error."""
        raise ToolException('SENSITIVE_HANDLED_ERROR')

    recoverable_probe.handle_tool_error = True
    agent = build_fake_agent(recoverable_probe, [
        AIMessage(content='', tool_calls=[
            {'name': 'recoverable_probe', 'args': {}, 'id': 'call-3'},
        ]),
        AIMessage(content='final-after-error'),
    ])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = invoke_agent_run(
            agent, [('user', 'probe')],
            session_id='session-3', thread_id='INTERNAL_THREAD', run_id='run-3',
        )

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


def test_direct_invoke_does_not_invent_run_id_for_tool_events(caplog):
    @tool
    def plain_probe() -> str:
        """Return a synthetic value."""
        return 'value'

    agent = build_fake_agent(plain_probe, [
        AIMessage(content='', tool_calls=[
            {'name': 'plain_probe', 'args': {}, 'id': 'call-4'},
        ]),
        AIMessage(content='final'),
    ])

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        agent.invoke({'messages': [('user', 'probe')]}, context={'session_id': 'session-4'})

    assert [r.tool_event for r in caplog.records if hasattr(r, 'tool_event')] == [
        {'event': 'tool_started', 'tool_name': 'plain_probe', 'tool_call_id': 'call-4'},
        {'event': 'tool_completed', 'tool_name': 'plain_probe', 'tool_call_id': 'call-4'},
    ]
