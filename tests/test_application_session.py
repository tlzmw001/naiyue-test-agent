import logging
from threading import Event, Thread

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from naiyue_test_agent.agent_runtime import build_runtime_agent
from naiyue_test_agent.application import SessionBusyError, SessionService
from naiyue_test_agent.model_access import ModelCatalog, ModelConfigError


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_sessions_continue_history_switch_models_and_isolate_threads(caplog):
    models = {
        'A': FakeMessagesListChatModel(responses=[
            AIMessage(content='A-one'), AIMessage(content='A-other'),
        ]),
        'B': FakeMessagesListChatModel(responses=[AIMessage(content='B-two')]),
    }
    service = SessionService(ModelCatalog('A', models))
    agent = build_runtime_agent(
        models['A'], tools=[], system_prompt='', response_format=None,
        resolve_model=service.resolve_model,
        record_selection=lambda _session_id, _model_id: None,
        checkpointer=InMemorySaver(),
    )
    first_session = service.create_session('A')
    other_session = service.create_session('A')
    assert first_session != other_session

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        first = service.run(agent, first_session, [('user', 'private-first')])
        service.select_model(first_session, 'B')
        second = service.run(agent, first_session, [('user', 'private-second')])
        other = service.run(agent, other_session, [('user', 'private-other')])

    assert [m.content for m in first['messages']] == ['private-first', 'A-one']
    assert [m.content for m in second['messages']] == [
        'private-first', 'A-one', 'private-second', 'B-two',
    ]
    assert [m.content for m in other['messages']] == ['private-other', 'A-other']
    selected = [r.model_selection['model_id'] for r in caplog.records
                if hasattr(r, 'model_selection')]
    assert selected == ['A', 'B', 'A']
    run_events = [r.run_event for r in caplog.records if hasattr(r, 'run_event')]
    assert [event['event'] for event in run_events] == [
        'run_started', 'run_completed', 'run_started', 'run_completed',
        'run_started', 'run_completed',
    ]
    run_ids = [event['run_id'] for event in run_events[::2]]
    assert len(set(run_ids)) == 3
    assert [event['run_id'] for event in run_events[1::2]] == run_ids
    assert [event['session_id'] for event in run_events] == [
        first_session, first_session, first_session, first_session,
        other_session, other_session,
    ]
    assert 'private-first' not in caplog.text
    assert 'private-second' not in caplog.text
    assert 'private-other' not in caplog.text
    assert 'A-one' not in caplog.text


def test_invalid_model_and_unknown_session_do_not_change_selection():
    models = {'A': FakeMessagesListChatModel(responses=[]),
              'B': FakeMessagesListChatModel(responses=[])}
    service = SessionService(ModelCatalog('A', models))
    with pytest.raises(ModelConfigError):
        service.create_session('missing')
    session_id = service.create_session('A')
    with pytest.raises(ModelConfigError):
        service.select_model(session_id, 'missing')
    assert service.resolve_model(session_id) == ('A', models['A'])
    with pytest.raises(KeyError):
        service.resolve_model('not-a-session')
    with pytest.raises(KeyError):
        service.select_model('not-a-session', 'B')

    class UnexpectedAgent:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError('unknown session must not call agent')

    with pytest.raises(KeyError):
        service.run(UnexpectedAgent(), 'not-a-session', [('user', 'private')])


def test_model_change_during_tool_call_applies_at_next_model_boundary(caplog):
    models = {
        'A': ToolCapableFake(responses=[AIMessage(
            content='', tool_calls=[{'name': 'switch_to_b', 'args': {}, 'id': 'call-1'}],
        )]),
        'B': ToolCapableFake(responses=[AIMessage(content='B-final')]),
    }
    service = SessionService(ModelCatalog('A', models))
    session_id = service.create_session('A')

    @tool
    def switch_to_b() -> str:
        """Switch the current test session to model B."""
        service.select_model(session_id, 'B')
        return 'switched'

    selected = []
    agent = build_runtime_agent(
        models['A'], tools=[switch_to_b], system_prompt='', response_format=None,
        resolve_model=service.resolve_model,
        record_selection=lambda _session_id, model_id: selected.append(model_id),
        checkpointer=InMemorySaver(),
    )
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = service.run(agent, session_id, [('user', 'switch after tool')])
    assert selected == ['A', 'B']
    assert [message.type for message in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert result['messages'][2].tool_call_id == result['messages'][1].tool_calls[0]['id']
    run_events = [r.run_event for r in caplog.records if hasattr(r, 'run_event')]
    tool_events = [r.tool_event for r in caplog.records if hasattr(r, 'tool_event')]
    assert [event['event'] for event in tool_events] == ['tool_started', 'tool_completed']
    assert [event['run_id'] for event in tool_events] == [run_events[0]['run_id']] * 2
    assert [event['session_id'] for event in run_events] == [session_id, session_id]
    assert result['messages'][-1].content == 'B-final'


def test_same_session_busy_rejected_other_session_runs_and_failure_releases_gate(caplog):
    service = SessionService(ModelCatalog('A', {
        'A': FakeMessagesListChatModel(responses=[]),
    }))
    first_session = service.create_session('A')
    other_session = service.create_session('A')
    started = Event()
    release = Event()
    failure = RuntimeError('private-failure')
    seen = []

    class BlockingAgent:
        def invoke(self, _input, *, context, config):
            seen.append((context['session_id'], config['configurable']['thread_id']))
            if context['session_id'] == first_session:
                started.set()
                assert release.wait(timeout=3)
                raise failure
            return {'messages': []}

    agent = BlockingAgent()
    worker_errors = []

    def run_first():
        try:
            service.run(agent, first_session, [('user', 'private')])
        except Exception as error:
            worker_errors.append(error)

    worker = Thread(target=run_first, daemon=True)
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        worker.start()
        try:
            assert started.wait(timeout=3)
            with pytest.raises(SessionBusyError):
                service.run(agent, first_session, [('user', 'second')])
            assert service.run(agent, other_session, [('user', 'other')]) == {'messages': []}
        finally:
            release.set()
            worker.join(timeout=3)

    assert not worker.is_alive()
    assert worker_errors == [failure]
    assert [item[0] for item in seen] == [first_session, other_session]
    assert seen[0][1] != seen[1][1]
    assert seen[0][1] not in caplog.text
    assert seen[1][1] not in caplog.text
    assert 'private-failure' not in caplog.text
    with pytest.raises(RuntimeError) as caught:
        service.run(agent, first_session, [('user', 'again')])
    assert caught.value is failure
