"""Follow-up waits for a natural end and continues the same run."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.application import prepare_tool_registry

from naiyue_test_agent.tools import ToolSpec

import logging
from threading import Event, Thread

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def build_app(monkeypatch, models, tools=None):
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', models))
    return application.build_local_application(
        'synthetic.json', tools=tools or [], system_prompt='', response_format=None,
    )


def run_in_worker(app, session_id):
    outcomes = []

    def run():
        try:
            outcomes.append(('result', app.run(session_id, [('user', 'initial')])))
        except Exception as error:
            outcomes.append(('error', error))

    worker = Thread(target=run, daemon=True)
    worker.start()
    return worker, outcomes


def test_two_follow_ups_are_fifo_separate_turns_in_one_run(monkeypatch, caplog):
    entered, release = Event(), Event()
    inputs = []

    class BlockingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            inputs.append([message.content for message in messages])
            if len(inputs) == 1:
                entered.set()
                assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = BlockingModel(responses=[AIMessage(content='answer-one'),
                                     AIMessage(content='answer-two'),
                                     AIMessage(content='answer-three')])
    app = build_app(monkeypatch, {'A': model})
    session_id = app.create_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert entered.wait(timeout=3)
            app.follow_up(session_id, 'private-follow-one')
            app.follow_up(session_id, 'private-follow-two')
        finally:
            release.set()
            worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcomes[0][0] == 'result', outcomes
    assert [m.content for m in outcomes[0][1]['messages']] == [
        'initial', 'answer-one', 'private-follow-one', 'answer-two',
        'private-follow-two', 'answer-three',
    ]
    assert inputs[0] == ['', 'initial']
    assert inputs[1][-1] == 'private-follow-one'
    assert inputs[2][-1] == 'private-follow-two'
    run_events = [r.run_event for r in caplog.records if hasattr(r, 'run_event')]
    assert [e['event'] for e in run_events] == ['run_started', 'run_completed']
    assert run_events[0]['run_id'] == run_events[1]['run_id']
    follow_events = [r.follow_up_event for r in caplog.records
                     if hasattr(r, 'follow_up_event')]
    assert [e['event'] for e in follow_events] == [
        'follow_up_accepted', 'follow_up_accepted',
        'follow_up_injected', 'follow_up_injected',
    ]
    assert {e['run_id'] for e in follow_events} == {run_events[0]['run_id']}
    assert 'private-follow-one' not in caplog.text


def test_follow_up_does_not_enter_model_immediately_after_tool(monkeypatch):
    tool_started, release_tool = Event(), Event()
    inputs = []

    @tool
    def probe() -> str:
        """Read a synthetic probe value."""
        tool_started.set()
        assert release_tool.wait(timeout=3)
        return 'tool-result'

    class RecordingModel(ToolCapableFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            inputs.append([message.content for message in messages])
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = RecordingModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'probe', 'args': {}, 'id': 'call-1'}]),
        AIMessage(content='tool-answer'), AIMessage(content='follow-answer'),
    ])
    app = build_app(monkeypatch, {'A': model}, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])
    session_id = app.create_session('A')
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert tool_started.wait(timeout=3)
        app.follow_up(session_id, 'private-follow')
    finally:
        release_tool.set()
        worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcomes[0][0] == 'result', outcomes
    assert 'private-follow' not in inputs[1]
    assert inputs[1][-1] == 'tool-result'
    assert inputs[2][-1] == 'private-follow'
    assert [m.type for m in outcomes[0][1]['messages']] == [
        'human', 'ai', 'tool', 'ai', 'human', 'ai',
    ]


def test_steering_has_priority_over_follow_up_at_natural_end(monkeypatch):
    entered, release = Event(), Event()
    calls = []

    class BlockingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                entered.set()
                assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = BlockingModel(responses=[AIMessage(content='first'),
                                     AIMessage(content='after-steering'),
                                     AIMessage(content='after-follow-up')])
    app = build_app(monkeypatch, {'A': model})
    session_id = app.create_session('A')
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert entered.wait(timeout=3)
        app.follow_up(session_id, 'private-follow')
        app.steer(session_id, 'private-steer')
    finally:
        release.set()
        worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcomes[0][0] == 'result', outcomes
    assert [m.content for m in outcomes[0][1]['messages']] == [
        'initial', 'first', 'private-steer', 'after-steering',
        'private-follow', 'after-follow-up',
    ]


def test_failure_discards_pending_follow_up_and_rejects_idle_input(monkeypatch, caplog):
    entered, release = Event(), Event()
    failure = RuntimeError('private-model-failure')

    class FailingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            raise failure

    app = build_app(monkeypatch, {'A': FailingModel(responses=[])})
    session_id = app.create_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert entered.wait(timeout=3)
            app.follow_up(session_id, 'private-pending-follow')
        finally:
            release.set()
            worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcomes == [('error', failure)]
    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_failed']
    follow_events = [r.follow_up_event for r in caplog.records
                     if hasattr(r, 'follow_up_event')]
    assert [e['event'] for e in follow_events] == [
        'follow_up_accepted', 'follow_up_discarded',
    ]
    assert follow_events[-1]['count'] == 1
    assert 'private-pending-follow' not in caplog.text
    with pytest.raises(application.SessionBusyError):
        app.follow_up(session_id, 'after-run')
    with pytest.raises(ValueError):
        app.follow_up(session_id, '')
    with pytest.raises(KeyError):
        app.follow_up('unknown-session', 'message')


def test_follow_up_is_isolated_between_active_sessions(monkeypatch):
    entered_a, entered_b, release = Event(), Event(), Event()

    def blocking_model(entered):
        calls = []

        class Model(FakeMessagesListChatModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                calls.append(messages)
                if len(calls) == 1:
                    entered.set()
                    assert release.wait(timeout=3)
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        return Model(responses=[AIMessage(content='first'),
                                AIMessage(content='second')])

    app = build_app(monkeypatch, {
        'A': blocking_model(entered_a), 'B': blocking_model(entered_b),
    })
    session_a = app.create_session('A')
    session_b = app.create_session('B')
    worker_a, outcomes_a = run_in_worker(app, session_a)
    worker_b, outcomes_b = run_in_worker(app, session_b)
    try:
        assert entered_a.wait(timeout=3)
        assert entered_b.wait(timeout=3)
        app.follow_up(session_a, 'private-A-only')
    finally:
        release.set()
        worker_a.join(timeout=3)
        worker_b.join(timeout=3)

    assert not worker_a.is_alive() and not worker_b.is_alive()
    assert outcomes_a[0][0] == outcomes_b[0][0] == 'result'
    assert [m.content for m in outcomes_a[0][1]['messages']] == [
        'initial', 'first', 'private-A-only', 'second',
    ]
    assert [m.content for m in outcomes_b[0][1]['messages']] == ['initial', 'first']


def test_natural_end_closes_follow_up_before_busy_gate_is_released():
    service = application.SessionService(ModelCatalog('A', {
        'A': FakeMessagesListChatModel(responses=[]),
    }))
    session_id = service.create_session('A')
    closed, release = Event(), Event()

    class PausedAtEndAgent:
        def invoke(self, _input, *, context, config):
            assert service.finish_run_boundary(context['session_id']) == (False, [])
            closed.set()
            assert release.wait(timeout=3)
            return {'messages': []}

    app = application.LocalApplication('A', service, PausedAtEndAgent(), prepare_tool_registry([]))
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert closed.wait(timeout=3)
        with pytest.raises(application.SessionBusyError):
            app.follow_up(session_id, 'arrived-after-close')
    finally:
        release.set()
        worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcomes == [('result', {'messages': []})]


def test_recursion_limit_is_a_failed_run_not_a_completed_answer(monkeypatch, caplog):
    app = None
    session_id = None

    class RequeueingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            app.follow_up(session_id, 'continue')
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    app = build_app(monkeypatch, {
        'A': RequeueingModel(responses=[AIMessage(content='partial')]),
    })
    session_id = app.create_session('A')
    agent = app._agent

    class LimitedAgent:
        def invoke(self, input, *, context, config):
            return agent.invoke(input, context=context,
                                config={**config, 'recursion_limit': 5})

    app._agent = LimitedAgent()
    with caplog.at_level(logging.INFO), pytest.raises(GraphRecursionError):
        app.run(session_id, [('user', 'initial')])

    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_failed']
    with pytest.raises(application.SessionBusyError):
        app.follow_up(session_id, 'after-failure')
