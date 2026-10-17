"""Deterministic control races use barriers/events rather than sleep windows."""

from threading import Barrier, Event, Thread

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.run_events import RunObservation
from test_application_control_events import blocked_app, controls, finish
from test_application_run_events import ToolModel, make_app
from test_turn_failure_boundaries import make_approval


def worker_run(app, session, outcomes):
    try:
        outcomes.append(app.run(session, [('user', 'initial')]))
    except BaseException as error:
        outcomes.append(error)


def test_control_accepted_before_start_is_buffered_until_run_started(monkeypatch):
    entered, release = Event(), Event()
    original = RunObservation.start

    def delayed_start(self):
        entered.set()
        assert release.wait(timeout=5)
        original(self)

    monkeypatch.setattr(RunObservation, 'start', delayed_start)
    app = make_app(monkeypatch)
    session = app.create_session('A')
    events, outcomes = [], []
    app.subscribe_events(events.append)
    worker = Thread(target=worker_run, args=(app, session, outcomes))
    worker.start()
    try:
        assert entered.wait(timeout=5)
        app.steer(session, 'queued before started')
        assert events == []
    finally:
        release.set()
        worker.join(timeout=6)
    assert not worker.is_alive() and len(outcomes) == 1 and isinstance(outcomes[0], dict), outcomes
    assert [e.event_type for e in events[:2]] == ['run_started', 'control_accepted']
    assert len(controls(events, 'control_delivered')) == 1
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))


def test_delayed_accept_notification_cannot_be_overtaken_by_delivery(monkeypatch):
    # Distinct response identities prevent the fake model from replacing its first AI.
    app, session, events, outcomes, worker, release_model, _calls = blocked_app(monkeypatch, [
        AIMessage(id='first', content='done'), AIMessage(id='second', content='done'),
    ])
    entered, release_accept = Event(), Event()
    original = app._sessions._record_steering
    accepted_errors = []

    def pause_accept(event, *args):
        if event == 'steering_accepted':
            entered.set()
            assert release_accept.wait(timeout=5)
        original(event, *args)

    monkeypatch.setattr(app._sessions, '_record_steering', pause_accept)

    def accept():
        try:
            app.steer(session, 'delayed notification')
        except BaseException as error:
            accepted_errors.append(error)

    sender = Thread(target=accept)
    sender.start()
    try:
        assert entered.wait(timeout=5)
        result = finish(worker, release_model, outcomes)
        assert result['messages'][-1].content == 'done'
        observed = controls(events)
        assert [e.event_type for e in observed] == ['control_accepted', 'control_delivery_started', 'control_delivered']
        assert len({e.data.control_id for e in observed}) == 1
    finally:
        release_model.set()
        release_accept.set()
        sender.join(timeout=6)
        worker.join(timeout=6)
    assert not sender.is_alive() and accepted_errors == []
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))


@pytest.mark.parametrize('kind', ['steering', 'follow_up', 'stop'])
def test_failure_discards_unprocessed_request_and_preserves_primary(monkeypatch, kind):
    entered, release = Event(), Event()
    failure = TimeoutError('PRIVATE_FAILURE')

    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            entered.set()
            assert release.wait(timeout=5)
            raise failure

    app = make_app(monkeypatch, Failing(responses=[AIMessage(content='unused')]))
    session = app.create_session('A')
    events, outcomes = [], []
    app.subscribe_events(events.append)
    worker = Thread(target=worker_run, args=(app, session, outcomes))
    worker.start()
    try:
        assert entered.wait(timeout=5)
        if kind == 'steering':
            app.steer(session, 'PRIVATE_STEER')
        elif kind == 'follow_up':
            app.follow_up(session, 'PRIVATE_FOLLOW')
        else:
            app.request_stop(session)
    finally:
        release.set()
        worker.join(timeout=6)
    assert not worker.is_alive() and outcomes == [failure]
    observed = controls(events)
    assert [e.event_type for e in observed] == ['control_accepted', 'control_discarded']
    assert observed[-1].data.reason == 'run_failed' and observed[-1].data.kind == kind
    assert observed[0].data.control_id == observed[1].data.control_id
    assert observed[-1].sequence < events[-1].sequence and events[-1].data.status == 'failed'
    assert session not in app._sessions._active_runs


def test_approval_preparation_failure_is_discarded_not_submitted_or_applied(monkeypatch, tmp_path):
    from naiyue_test_agent.application import session_service

    app, session, events = make_approval(monkeypatch, tmp_path)
    count = len(events)
    failure = OSError('PRIVATE_PREPARE')

    def fail_prepare(*args, **kwargs):
        raise failure

    monkeypatch.setattr(session_service, 'prepare_run_resources', fail_prepare)
    with pytest.raises(OSError) as caught:
        app.resume_tool_approval(session, ['approve'])
    assert caught.value is failure
    observed = controls(events[count:])
    assert [e.event_type for e in observed] == ['control_accepted', 'control_discarded']
    assert observed[-1].data.reason == 'prepare_failed'
    assert app.pending_tool_approvals(session)


def test_active_stop_before_approval_skips_tool_and_finishes(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import create_session_read_tool
    from naiyue_test_agent.model_access import ModelCatalog

    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('body', encoding='utf-8')
    entered, release = Event(), Event()

    class Blocking(ToolModel):
        def _generate(self, *args, **kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return super()._generate(*args, **kwargs)

    model = Blocking(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read'},
    ])])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    app = application.build_local_application('synthetic.json', tools=[create_session_read_tool()],
                                               system_prompt='', response_format=None)
    session = app.create_workspace_session('A', root)
    events, outcomes = [], []
    app.subscribe_events(events.append)
    worker = Thread(target=worker_run, args=(app, session, outcomes))
    worker.start()
    try:
        assert entered.wait(timeout=5)
        app.request_stop(session)
        result = finish(worker, release, outcomes)
    finally:
        release.set()
        worker.join(timeout=6)
    assert 'approval_requests' not in result
    result_message = result['messages'][-1]
    assert result_message.type == 'tool' and result_message.tool_call_id == 'read'
    assert result_message.status == 'error' and 'user stopped' in result_message.content
    assert [e.event_type for e in controls(events)] == ['control_accepted', 'control_applied']
    assert controls(events)[-1].data.boundary == 'active'
    assert events[-1].data.status == 'stopped'


def test_rejected_control_api_calls_never_create_accepted_events(monkeypatch):
    app = make_app(monkeypatch)
    session = app.create_session('A')
    events = []
    app.subscribe_events(events.append)
    for callback in (lambda: app.steer(session, 'idle'), lambda: app.follow_up(session, 'idle'),
                     lambda: app.request_stop(session)):
        with pytest.raises(application.SessionBusyError):
            callback()
    with pytest.raises(ValueError):
        app.steer(session, '')
    with pytest.raises(application.SessionPendingApprovalError):
        app.resume_tool_approval(session, ['approve'])
    assert events == []


def test_control_callbacks_run_without_session_lock(monkeypatch):
    entered, release = Event(), Event()

    class Blocking(ToolModel):
        def _generate(self, *args, **kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return super()._generate(*args, **kwargs)

    app = make_app(monkeypatch, Blocking(responses=[
        AIMessage(id='one', content='one'), AIMessage(id='two', content='two'),
    ]))
    session = app.create_session('A')
    outcomes = []
    lock_states = []

    def observe(event):
        if event.event_type.startswith('control_'):
            acquired = app._sessions._lock.acquire(blocking=False)
            lock_states.append(acquired)
            if acquired:
                app._sessions._lock.release()

    app.subscribe_events(observe)
    worker = Thread(target=worker_run, args=(app, session, outcomes))
    worker.start()
    try:
        assert entered.wait(timeout=5)
        app.steer(session, 'check lock')
        finish(worker, release, outcomes)
    finally:
        release.set()
        worker.join(timeout=6)
    assert lock_states == [True, True, True]


def test_old_cleanup_control_notifications_keep_old_run_after_new_run_starts(monkeypatch):
    app, session, events, outcomes, worker, release, _calls = blocked_app(monkeypatch, [
        AIMessage(id='old-ai', content='old'), AIMessage(id='new-ai', content='new'),
    ])
    cleanup_entered, release_cleanup = Event(), Event()
    original = app._sessions._record_steering

    def pause_discard(event, *args):
        if event == 'steering_discarded':
            cleanup_entered.set()
            assert release_cleanup.wait(timeout=5)
        original(event, *args)

    monkeypatch.setattr(app._sessions, '_record_steering', pause_discard)
    old_run = events[0].run_id
    try:
        app.steer(session, 'old queued')
        app.request_stop(session)
        release.set()
        assert cleanup_entered.wait(timeout=5)
        new_result = app.run(session, [('user', 'new')])
        new_start = next(e for e in events if e.event_type == 'run_started' and e.run_id != old_run)
        assert new_result['messages'][-1].content == 'new'
    finally:
        release.set()
        release_cleanup.set()
        worker.join(timeout=6)
    assert not worker.is_alive() and len(outcomes) == 1 and isinstance(outcomes[0], dict), outcomes
    discarded = controls(events, 'control_discarded')
    assert len(discarded) == 1 and discarded[0].run_id == old_run
    assert discarded[0].data.reason == 'run_stopped'
    assert not controls([e for e in events if e.run_id == new_start.run_id])
    assert session not in app._sessions._active_runs


def test_two_sessions_have_independent_control_ids_and_sequences(monkeypatch):
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.messages import HumanMessage

    entered = Barrier(3)
    release = Event()
    responses = []

    class Concurrent(ToolModel):
        def _generate(self, messages, *args, **kwargs):
            humans = [m for m in messages if isinstance(m, HumanMessage)]
            if len(humans) == 1:
                entered.wait(timeout=5)
                assert release.wait(timeout=5)
            responses.append(True)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='done'))])

    app = make_app(monkeypatch, Concurrent(responses=[AIMessage(content='unused')]))
    sessions = [app.create_session('A') for _ in range(2)]
    events, outcomes = [], []
    app.subscribe_events(events.append)
    workers = [Thread(target=worker_run, args=(app, session, outcomes)) for session in sessions]
    for worker in workers:
        worker.start()
    try:
        entered.wait(timeout=5)
        for session in sessions:
            app.steer(session, 'same')
    finally:
        release.set()
        for worker in workers:
            worker.join(timeout=6)
    assert all(not w.is_alive() for w in workers)
    assert len(outcomes) == 2 and all(isinstance(r, dict) for r in outcomes), outcomes
    assert len(responses) == 4
    accepted = controls(events, 'control_accepted')
    assert len({e.data.control_id for e in accepted}) == 2
    for session in sessions:
        scoped = [e for e in events if e.session_id == session]
        assert [e.event_type for e in controls(scoped)] == [
            'control_accepted', 'control_delivery_started', 'control_delivered',
        ]
        assert [e.sequence for e in scoped] == list(range(1, len(scoped) + 1))


def test_control_consumer_failure_is_isolated_from_delivery_and_result(monkeypatch, caplog):
    calls = []

    def broken(event):
        if event.event_type.startswith('control_'):
            calls.append(event.event_type)
            raise RuntimeError('PRIVATE_CONSUMER')

    app, session, events, outcomes, worker, release, _calls = blocked_app(monkeypatch, [
        AIMessage(id='one', content='one'), AIMessage(id='two', content='two'),
    ], observer=broken)
    try:
        app.steer(session, 'PRIVATE_INPUT')
        result = finish(worker, release, outcomes)
    finally:
        release.set()
        worker.join(timeout=6)
    assert calls == ['control_accepted']
    assert result['messages'][-1].content == 'two'
    assert controls(events)[-1].event_type == 'control_delivered'
    assert 'event_observer_failed' in caplog.text and 'PRIVATE_' not in caplog.text


def test_control_publish_failure_does_not_undo_accepted_input_or_fail_run(monkeypatch, caplog):
    app, session, events, outcomes, worker, release, _calls = blocked_app(monkeypatch, [
        AIMessage(id='one', content='one'), AIMessage(id='two', content='two'),
    ])
    publisher = app._sessions._active_runs[session].publisher
    original = publisher.publish

    def fail_control(event_type, data):
        if event_type.startswith('control_'):
            raise RuntimeError('PRIVATE_PUBLISH')
        return original(event_type, data)

    monkeypatch.setattr(publisher, 'publish', fail_control)
    try:
        app.steer(session, 'still delivered')
        result = finish(worker, release, outcomes)
    finally:
        release.set()
        worker.join(timeout=6)
    assert [m.content for m in result['messages'] if m.type == 'human'] == ['initial', 'still delivered']
    assert events[-1].data.status == 'completed' and session not in app._sessions._active_runs
    assert 'control_event_publish_failed' in caplog.text and 'PRIVATE_' not in caplog.text


def test_delivery_before_next_model_failure_is_not_relabelled_as_discard(monkeypatch):
    entered, release = Event(), Event()
    failure = TimeoutError('synthetic')
    calls = []

    class SecondFails(ToolModel):
        def _generate(self, *args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                assert release.wait(timeout=5)
                return super()._generate(*args, **kwargs)
            raise failure

    app = make_app(monkeypatch, SecondFails(responses=[AIMessage(content='first')]))
    session = app.create_session('A')
    events, outcomes = [], []
    app.subscribe_events(events.append)
    worker = Thread(target=worker_run, args=(app, session, outcomes))
    worker.start()
    try:
        assert entered.wait(timeout=5)
        app.steer(session, 'delivered')
    finally:
        release.set()
        worker.join(timeout=6)
    assert not worker.is_alive() and outcomes == [failure]
    assert [e.event_type for e in controls(events)] == [
        'control_accepted', 'control_delivery_started', 'control_delivered',
    ]
    assert events[-1].data.status == 'failed'
