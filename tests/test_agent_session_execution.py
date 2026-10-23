"""The session owns its current run, controls and required finish."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent.application.errors import SessionBusyError
from test_agent_session import make_service
from test_application_run_events import make_app
from test_async_persistent import factory, StreamingToolModel
from test_session_state import restored_config


def test_admission_installs_observation_on_its_session_only():
    service = make_service()
    first, second = service.create_session('A'), service.create_session('A')
    owner, observation_run = service._admit(first, 'run-a', 'message')
    rid, observation = observation_run.run_id, observation_run.observation
    other = service._sessions[second]
    assert owner._run.observation is observation
    assert owner.is_busy() and owner.snapshot_for_run()['run_id'] == rid
    assert other._run is None and not other.is_busy()
    assert not hasattr(service, '_active_runs')
    with pytest.raises(SessionBusyError):
        service._admit(first, 'duplicate', 'message')
    assert owner._run.observation is observation


def test_invalid_resume_does_not_install_an_observation():
    service = make_service()
    pending = {'batch_id': 'batch', 'requested_run_id': 'old',
        'items': [{'tool_call_id': 'call', 'tool_name': 'read', 'target': 'file', 'workspace': None}]}
    service.install_restored_session('saved', **restored_config(), pending=pending)
    owner, events = service._sessions['saved'], []
    service.subscribe_events(events.append)
    with pytest.raises(ValueError, match='One approve/reject'):
        service._admit('saved', 'invalid', 'approval_resume', decisions=[])
    assert owner._run is None and not owner.is_busy()
    assert owner.snapshot_for_run()['pending'] == pending and events == []


def test_old_cleanup_cannot_clear_a_new_current_run():
    service = make_service()
    sid = service.create_session('A')
    owner, old_run = service._admit(sid, 'old', 'message')
    rid, old = old_run.run_id, old_run.observation
    assert owner._cleanup_run(old_run) is False
    owner.release_run(old_run)
    assert owner._run is None and not owner.is_busy()
    _, current_run = service._admit(sid, 'new', 'message')
    current_id, current = current_run.run_id, current_run.observation
    with pytest.raises(SessionBusyError):
        owner._cleanup_run(old_run)
    with pytest.raises(SessionBusyError):
        owner.release_run(old_run)
    assert owner._run is current_run and owner.snapshot_for_run()['run_id'] == current_id


def test_control_uses_owned_observation_and_flushes_without_state_lock():
    service = make_service()
    sid = service.create_session('A')
    checks = []
    with ThreadPoolExecutor(max_workers=1) as worker:
        def handler(event):
            if event.event_type == 'control_accepted':
                checks.append(worker.submit(service.require(sid).describe_session).result(timeout=5))
        service.subscribe_events(handler)
        owner, observation_run = service._admit(sid, 'run', 'message')
        rid, observation = observation_run.run_id, observation_run.observation
        observation.start()
        owner.steer('change direction')
        owner.follow_up('next task')
        assert [item.text for item in service.drain_steering(sid)] == ['change direction']
        stopped, queued = service.finish_run_boundary(sid)
        assert stopped is False and [(item.kind, item.text) for item in queued] == [('follow_up', 'next task')]
        assert queued[0].message_id
    assert len(checks) == 2
    assert all(row['run_id'] == rid and row['busy'] for row in checks)
    owner._cleanup_run(observation_run)


@async_test
async def test_required_save_keeps_busy_after_control_window_is_closed(monkeypatch, tmp_path):
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    async with factory(monkeypatch, tmp_path / 'sessions.sqlite',
                       StreamingToolModel(replies=[AIMessage(content='answer')])) as app:
        sid = await app.sessions.create('A')
        owner, events = app.sessions._sessions._sessions[sid], []
        app.observability.subscribe(events.append)
        original = app.sessions._persistence.storage.runs.finish_run
        def save(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return original(*args, **kwargs)
        monkeypatch.setattr(app.sessions._persistence.storage.runs, 'finish_run', save)
        task = asyncio.create_task(app.runs.start(sid, [('user', 'question')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert owner._run is not None and not owner._run.controls_open and owner.is_busy() and not task.done()
            assert not any(event.event_type == 'run_finished' for event in events)
            with pytest.raises(SessionBusyError):
                app.sessions._sessions._admit(sid, 'duplicate', 'message')
        finally:
            release.set()
            await task
        assert owner._run is None and not owner.is_busy()
        assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'completed'


@pytest.mark.parametrize('fails', [False, True])
@async_test
async def test_execution_uses_owned_child_lifecycle(monkeypatch, fails):
    from naiyue_test_agent.application.sessions import session_execution
    app = await make_app(monkeypatch)
    sid = await app.sessions.create('A')
    service, calls = app.sessions._sessions, []
    owner, observation_run = service._admit(sid, 'owned-run', 'message')
    rid, observation = observation_run.run_id, observation_run.observation
    failure = RuntimeError('synthetic execution failure')
    async def invoke_child(*args, **kwargs):
        pytest.fail('The model did not request a child')
    async def settle(run_id):
        calls.append(('settle', sid, run_id))
    def settled(run_id):
        calls.append(('assert', sid, run_id))
    def stop(**kwargs):
        calls.append(('stop', sid, kwargs['run_id']))
    original_stop = owner.request_scope_stop
    monkeypatch.setattr(owner, '_invoke_subagent', invoke_child)
    monkeypatch.setattr(owner, 'request_scope_stop', stop)
    monkeypatch.setattr(owner, 'settle_subagent_drivers', settle)
    monkeypatch.setattr(owner, 'assert_subagents_settled', settled)
    if fails:
        async def execute(*args, **kwargs):
            raise failure
        monkeypatch.setattr(session_execution, 'ainvoke_agent_run', execute)
        with pytest.raises(RuntimeError) as caught:
            await owner.arun(app.runs._agent, [('user', 'question')], run=observation_run)
        assert caught.value is failure
    else:
        result = await owner.arun(app.runs._agent, [('user', 'question')], run=observation_run)
        assert result['messages'][-1].content == 'PRIVATE_REPLY'
    expected = [('stop', sid, rid)] if fails else []
    assert calls == expected + [('settle', sid, rid), ('assert', sid, rid)]
    assert owner._run is None and not owner.is_busy()
    assert not hasattr(service, '_arun') and not hasattr(service, '_afinish_run')
    monkeypatch.setattr(owner, 'request_scope_stop', original_stop)
    await app.aclose()
