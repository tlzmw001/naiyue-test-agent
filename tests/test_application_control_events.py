"""Accepted controls are observed only at their real state/effect boundaries."""

import logging
from dataclasses import FrozenInstanceError
from threading import Event, Thread

import pytest
from langchain_core.messages import AIMessage

from test_application_run_events import ToolModel, make_app
from test_turn_failure_boundaries import make_approval


def controls(events, event_type=None):
    return [e for e in events if e.event_type.startswith('control_')
            and (event_type is None or e.event_type == event_type)]


def blocked_app(monkeypatch, responses=None, *, observer=None):
    entered, release = Event(), Event()
    calls = []

    class Blocking(ToolModel):
        def _generate(self, *args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                assert release.wait(timeout=5)
            return super()._generate(*args, **kwargs)

    app = make_app(monkeypatch, Blocking(responses=responses or [AIMessage(content='done')]))
    session = app.create_session('A')
    events, outcomes = [], []
    app.subscribe_events(events.append)
    if observer is not None:
        app.subscribe_events(observer)

    def run():
        try:
            outcomes.append(app.run(session, [('user', 'initial')]))
        except BaseException as error:
            outcomes.append(error)

    worker = Thread(target=run)
    worker.start()
    assert entered.wait(timeout=5)
    return app, session, events, outcomes, worker, release, calls


def finish(worker, release, outcomes):
    release.set()
    worker.join(timeout=6)
    assert not worker.is_alive() and len(outcomes) == 1 and isinstance(outcomes[0], dict), outcomes
    return outcomes[0]


def test_running_cli_thread_controls_deliver_in_priority_order_with_real_ids(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        app, session, events, outcomes, worker, release, calls = blocked_app(monkeypatch, [
            AIMessage(content='first'), AIMessage(content='second'), AIMessage(content='third'),
        ])
        try:
            app.follow_up(session, 'PRIVATE_FOLLOW')
            app.steer(session, 'PRIVATE_STEER')
            app.steer(session, 'PRIVATE_STEER')
            assert len(controls(events, 'control_accepted')) == 3
            assert controls(events, 'control_delivered') == []
            result = finish(worker, release, outcomes)
        finally:
            release.set()
            worker.join(timeout=6)
    assert len(calls) == 3
    assert [m.content for m in result['messages'] if m.type == 'human'] == [
        'initial', 'PRIVATE_STEER', 'PRIVATE_STEER', 'PRIVATE_FOLLOW',
    ]
    accepted = controls(events, 'control_accepted')
    delivered = controls(events, 'control_delivered')
    assert [e.data.kind for e in delivered] == ['steering', 'steering', 'follow_up']
    assert len({e.data.control_id for e in accepted}) == len({e.data.message_id for e in delivered}) == 3
    by_control = {e.data.control_id: e for e in accepted}
    for event in delivered:
        message = next(e for e in events if e.event_type == 'message_completed'
                       and e.data.message_id == event.data.message_id)
        assert by_control[event.data.control_id].sequence < message.sequence < event.sequence
        assert event.session_id == session and event.run_id == events[0].run_id
    assert events[-1].event_type == 'run_finished'
    assert not any(e.event_type in ('control_discarded', 'control_outcome_unknown') for e in controls(events))
    with pytest.raises(FrozenInstanceError):
        delivered[0].data.kind = 'approval'
    logs = [r.application_event for r in caplog.records if hasattr(r, 'application_event')]
    assert 'PRIVATE_' not in repr(controls(events)) + repr(logs) + caplog.text


def test_stop_is_idempotent_applied_from_result_and_discards_waiting_messages(monkeypatch):
    app, session, events, outcomes, worker, release, calls = blocked_app(monkeypatch)
    try:
        app.steer(session, 'discard steering')
        app.follow_up(session, 'discard follow')
        app.request_stop_after_turn(session)
        app.request_stop_after_turn(session)
        result = finish(worker, release, outcomes)
    finally:
        release.set()
        worker.join(timeout=6)
    assert len(calls) == 1 and result['run_stopped'] is True
    accepted = controls(events, 'control_accepted')
    assert [e.data.kind for e in accepted] == ['steering', 'follow_up', 'stop']
    applied = controls(events, 'control_applied')
    assert len(applied) == 1 and applied[0].data.kind == 'stop' and applied[0].data.boundary == 'after_turn'
    assert [e.data.kind for e in controls(events, 'control_discarded')] == ['steering', 'follow_up']
    assert all(e.data.reason == 'run_stopped' for e in controls(events, 'control_discarded'))
    assert events[-1].data.status == 'stopped'


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_approval_is_accepted_then_applied_at_actual_hook(monkeypatch, tmp_path, decision):
    app, session, events = make_approval(monkeypatch, tmp_path)
    requested = next(e for e in events if e.event_type == 'approval_requested')
    assert requested.data.tool_call_ids == ('read',)
    result = app.resume_tool_approval(session, [decision])
    received = controls(events)
    assert [e.event_type for e in received] == ['control_accepted', 'control_delivery_started', 'control_applied']
    assert all(e.data.kind == 'approval' and e.data.tool_call_ids == ('read',) for e in received)
    assert received[0].data.decisions == (decision,)
    assert received[-1].data.boundary == 'approval_hook'
    assert received[0].data.origin_run_id == requested.run_id != received[0].run_id
    assert len({e.data.control_id for e in received}) == 1
    assert result['messages'][-1].content == 'done'
    assert not any(e.event_type == 'control_outcome_unknown' for e in received)


def test_failed_tools_resume_without_replaying_hitl_does_not_fake_new_approval_applied(monkeypatch, tmp_path):
    from naiyue_test_agent.agent_runtime.tool_batch import ToolBatchMiddleware

    original = ToolBatchMiddleware.wrap_tool_call
    failure = RuntimeError('synthetic execution failure')
    attempts = []

    def fail_once(self, request, handler):
        attempts.append(True)
        if len(attempts) == 1:
            raise failure
        return original(self, request, handler)

    monkeypatch.setattr(ToolBatchMiddleware, 'wrap_tool_call', fail_once)
    app, session, events = make_approval(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError) as caught:
        app.resume_tool_approval(session, ['approve'])
    assert caught.value is failure
    assert len(controls(events, 'control_applied')) == 1
    count = len(events)
    result = app.resume_tool_approval(session, ['approve'])
    received = controls(events[count:])
    assert [e.event_type for e in received] == ['control_accepted', 'control_delivery_started', 'control_outcome_unknown']
    assert received[-1].data.reason == 'approval_resolution_unconfirmed'
    assert result['messages'][-1].content == 'done'
