"""Accepted controls are observed only at their real state/effect boundaries."""

import asyncio

from native_application_helpers import async_test

import logging
from dataclasses import FrozenInstanceError
from threading import Event

import pytest
from langchain_core.messages import AIMessage

from test_application_run_events import ToolModel, make_app
from test_turn_failure_boundaries import make_approval


def controls(events, event_type=None):
    return [e for e in events if e.event_type.startswith('control_')
            and (event_type is None or e.event_type == event_type)]


async def blocked_app(monkeypatch, responses=None, *, observer=None):
    entered, release = Event(), Event()
    calls = []

    class Blocking(ToolModel):
        def _generate(self, *args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                assert release.wait(timeout=5)
            return super()._generate(*args, **kwargs)

    app = await make_app(monkeypatch, Blocking(responses=responses or [AIMessage(content='done')]))
    session = await app.acreate_session('A')
    events, outcomes = [], []
    app.subscribe_events(events.append)
    if observer is not None:
        app.subscribe_events(observer)

    async def run():
        try:
            outcomes.append(await app.arun(session, [('user', 'initial')]))
        except BaseException as error:
            outcomes.append(error)

    worker = asyncio.create_task(run())

    assert await asyncio.to_thread(entered.wait, timeout=5)
    return app, session, events, outcomes, worker, release, calls


async def finish(worker, release, outcomes):
    release.set()
    await asyncio.wait_for(asyncio.shield(worker), 6)
    assert worker.done() and len(outcomes) == 1 and isinstance(outcomes[0], dict), outcomes
    return outcomes[0]


@async_test
async def test_running_cli_thread_controls_deliver_in_priority_order_with_real_ids(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        app, session, events, outcomes, worker, release, calls = await blocked_app(monkeypatch, [
            AIMessage(content='first'), AIMessage(content='second'), AIMessage(content='third'),
        ])
        try:
            app.follow_up(session, 'PRIVATE_FOLLOW')
            app.steer(session, 'PRIVATE_STEER')
            app.steer(session, 'PRIVATE_STEER')
            assert len(controls(events, 'control_accepted')) == 3
            assert controls(events, 'control_delivered') == []
            result = await finish(worker, release, outcomes)
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(worker), 6)
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


@async_test
async def test_stop_is_idempotent_applied_from_result_and_discards_waiting_messages(monkeypatch):
    app, session, events, outcomes, worker, release, calls = await blocked_app(monkeypatch)
    try:
        app.steer(session, 'discard steering')
        app.follow_up(session, 'discard follow')
        app.request_stop(session)
        app.request_stop(session)
        result = await finish(worker, release, outcomes)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(worker), 6)
    assert len(calls) == 1 and result['run_stopped'] is True
    accepted = controls(events, 'control_accepted')
    assert [e.data.kind for e in accepted] == ['steering', 'follow_up', 'stop']
    applied = controls(events, 'control_applied')
    assert len(applied) == 1 and applied[0].data.kind == 'stop' and applied[0].data.boundary == 'active'
    assert [e.data.kind for e in controls(events, 'control_discarded')] == ['steering', 'follow_up']
    assert all(e.data.reason == 'run_stopped' for e in controls(events, 'control_discarded'))
    assert events[-1].data.status == 'stopped'


@pytest.mark.parametrize('decision', ['approve', 'reject'])
@async_test
async def test_approval_is_accepted_then_applied_at_actual_hook(monkeypatch, tmp_path, decision):
    app, session, events = await make_approval(monkeypatch, tmp_path)
    requested = next(e for e in events if e.event_type == 'approval_requested')
    assert requested.data.tool_call_ids == ('read',)
    result = await app.aresume_tool_approval(session, [decision])
    received = controls(events)
    assert [e.event_type for e in received] == ['control_accepted', 'control_delivery_started', 'control_applied']
    assert all(e.data.kind == 'approval' and e.data.tool_call_ids == ('read',) for e in received)
    assert received[0].data.decisions == (decision,)
    assert received[-1].data.boundary == 'approval_hook'
    assert received[0].data.origin_run_id == requested.run_id != received[0].run_id
    assert len({e.data.control_id for e in received}) == 1
    assert result['messages'][-1].content == 'done'
    assert not any(e.event_type == 'control_outcome_unknown' for e in received)


@async_test
async def test_failed_tool_stage_blocks_replay_without_fake_approval_events(monkeypatch, tmp_path):
    from naiyue_test_agent.agent_runtime.tool_execution.batch import ToolBatchMiddleware

    original = ToolBatchMiddleware.awrap_tool_call
    failure = RuntimeError('synthetic execution failure')
    attempts = []

    async def fail_once(self, request, handler):
        attempts.append(True)
        if len(attempts) == 1:
            raise failure
        return await original(self, request, handler)

    monkeypatch.setattr(ToolBatchMiddleware, 'awrap_tool_call', fail_once)
    app, session, events = await make_approval(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError) as caught:
        await app.aresume_tool_approval(session, ['approve'])
    assert caught.value is failure
    assert len(controls(events, 'control_applied')) == 1
    count = len(events)
    from naiyue_test_agent.application import SessionManualReviewRequiredError
    with pytest.raises(SessionManualReviewRequiredError):
        await app.aresume_tool_approval(session, ['approve'])
    assert events[count:] == [] and attempts == [True]
    state = app._sessions._sessions[session]
    assert state.describe_session()['manual_review_required'] and not state.is_busy()
    assert state.snapshot_for_run()['pending'] is None
    assert len(controls(events, 'control_applied')) == 1
