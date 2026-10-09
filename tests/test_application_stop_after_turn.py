"""A stop request finishes the current turn without starting another."""

import logging
from threading import Event, Thread

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def build_app(monkeypatch, model, tools=None):
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
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


def test_stop_after_current_answer_discards_pending_messages(monkeypatch, caplog):
    entered, release = Event(), Event()

    class BlockingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    app = build_app(monkeypatch, BlockingModel(responses=[AIMessage(content='answer')]))
    session_id = app.create_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert entered.wait(timeout=3)
            app.steer(session_id, 'private-steering')
            app.follow_up(session_id, 'private-follow-up')
            app.request_stop_after_turn(session_id)
            app.request_stop_after_turn(session_id)
            with pytest.raises(application.SessionBusyError):
                app.steer(session_id, 'too-late')
        finally:
            release.set()
            worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcomes[0][0] == 'result', outcomes
    assert [m.content for m in outcomes[0][1]['messages']] == ['initial', 'answer']
    assert outcomes[0][1]['run_stopped'] is True
    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_stopped']
    stop_events = [r.stop_event['event'] for r in caplog.records if hasattr(r, 'stop_event')]
    assert stop_events == ['stop_requested']
    steering = [r.steering_event['event'] for r in caplog.records
                if hasattr(r, 'steering_event')]
    follow_up = [r.follow_up_event['event'] for r in caplog.records
                 if hasattr(r, 'follow_up_event')]
    assert steering == ['steering_accepted', 'steering_discarded']
    assert follow_up == ['follow_up_accepted', 'follow_up_discarded']
    assert 'private-steering' not in caplog.text
    assert 'private-follow-up' not in caplog.text


def test_stop_waits_for_current_tool_batch_and_skips_next_model(monkeypatch):
    first_started, second_started, release = Event(), Event(), Event()
    model_inputs = []

    @tool
    def first_tool() -> str:
        """Return first synthetic value."""
        first_started.set()
        assert release.wait(timeout=3)
        return 'first-result'

    @tool
    def second_tool() -> str:
        """Return second synthetic value."""
        second_started.set()
        assert release.wait(timeout=3)
        return 'second-result'

    class RecordingModel(ToolCapableFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            model_inputs.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = RecordingModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'first_tool', 'args': {}, 'id': 'call-one'},
        {'name': 'second_tool', 'args': {}, 'id': 'call-two'},
    ]), AIMessage(content='must-not-be-called')])
    app = build_app(monkeypatch, model, [first_tool, second_tool])
    session_id = app.create_session('A')
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert first_started.wait(timeout=3)
        assert second_started.wait(timeout=3)
        app.request_stop_after_turn(session_id)
    finally:
        release.set()
        worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcomes[0][0] == 'result', outcomes
    assert len(model_inputs) == 1
    result = outcomes[0][1]
    assert result['run_stopped'] is True
    assert [m.content for m in result['messages']] == [
        'initial', '', 'first-result', 'second-result',
    ]


def test_model_failure_wins_over_requested_stop(monkeypatch, caplog):
    entered, release = Event(), Event()
    failure = RuntimeError('private-model-failure')

    class FailingModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            raise failure

    app = build_app(monkeypatch, FailingModel(responses=[]))
    session_id = app.create_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert entered.wait(timeout=3)
            app.request_stop_after_turn(session_id)
        finally:
            release.set()
            worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcomes == [('error', failure)]
    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_failed']
    assert 'private-model-failure' not in caplog.text


def test_stop_state_resets_before_next_run(monkeypatch, caplog):
    entered, release = Event(), Event()
    calls = []

    class FirstCallBlocked(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                entered.set()
                assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    app = build_app(monkeypatch, FirstCallBlocked(responses=[
        AIMessage(content='first-answer'), AIMessage(content='second-answer'),
    ]))
    session_id = app.create_session('A')
    with caplog.at_level(logging.INFO):
        worker, outcomes = run_in_worker(app, session_id)
        try:
            assert entered.wait(timeout=3)
            app.request_stop_after_turn(session_id)
        finally:
            release.set()
            worker.join(timeout=3)
        second = app.run(session_id, [('user', 'second-task')])

    assert not worker.is_alive()
    assert outcomes[0][1]['run_stopped'] is True
    assert second['run_stopped'] is False
    assert [m.content for m in second['messages']] == [
        'initial', 'first-answer', 'second-task', 'second-answer',
    ]
    run_events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert run_events == ['run_started', 'run_stopped', 'run_started', 'run_completed']


def test_stop_rejects_idle_and_unknown_sessions(monkeypatch):
    app = build_app(monkeypatch, FakeMessagesListChatModel(responses=[]))
    session_id = app.create_session('A')
    with pytest.raises(application.SessionBusyError):
        app.request_stop_after_turn(session_id)
    with pytest.raises(KeyError):
        app.request_stop_after_turn('missing-session')


def test_stop_rejects_closed_window_while_run_is_still_busy():
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

    app = application.LocalApplication('A', service, PausedAtEndAgent())
    worker, outcomes = run_in_worker(app, session_id)
    try:
        assert closed.wait(timeout=3)
        with pytest.raises(application.SessionBusyError):
            app.request_stop_after_turn(session_id)
    finally:
        release.set()
        worker.join(timeout=3)

    assert not worker.is_alive()
    assert outcomes == [('result', {'messages': []})]


def test_stop_before_first_model_does_not_inject_queued_steering(monkeypatch):
    model = FakeMessagesListChatModel(responses=[AIMessage(content='first-answer')])
    app = build_app(monkeypatch, model)
    session_id = app.create_session('A')
    agent = app._agent

    class QueueBeforeInvoke:
        def invoke(self, input, *, context, config):
            app.steer(session_id, 'not-for-first-model')
            app.request_stop_after_turn(session_id)
            return agent.invoke(input, context=context, config=config)

    app._agent = QueueBeforeInvoke()
    result = app.run(session_id, [('user', 'initial')])

    assert [message.content for message in result['messages']] == [
        'initial', 'first-answer',
    ]
    assert result['run_stopped'] is True
