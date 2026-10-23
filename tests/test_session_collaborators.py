"""Explicit collaborators share one session's live ownership and short lock."""
import pytest

from test_agent_session import make_service
from naiyue_test_agent.application.errors import SessionBusyError


def test_sessions_compose_concrete_collaborators_with_one_runtime():
    from naiyue_test_agent.application.sessions.agent_session import AgentSession
    from naiyue_test_agent.application.sessions.session_service import SessionService
    assert AgentSession.__bases__ == (object,)
    assert SessionService.__bases__ == (object,)
    service = make_service()
    first, second = service.create_session('A'), service.create_session('A')
    a, b = service.require(first), service.require(second)
    assert a._runtime is not b._runtime and a._runtime.state is not b._runtime.state
    assert a._runtime.lock is not b._runtime.lock
    for owner in (a, b):
        for name in ('lifecycle', 'controls', 'approvals', 'subagents'):
            collaborator = getattr(owner, name)
            assert collaborator._runtime is owner._runtime
            assert type(collaborator).__bases__ == (object,)
            assert all(value is not owner and value is not service for value in vars(collaborator).values())


def test_control_port_routes_to_owning_queue_and_keeps_other_session_idle(monkeypatch):
    service = make_service()
    first, second = service.create_session('A'), service.create_session('A')
    owner, run = service._admit(first, 'root-run', 'message')
    observed = []
    original = owner.controls.drain_steering
    def drain():
        observed.append(owner._runtime.run)
        return original()
    monkeypatch.setattr(owner.controls, 'drain_steering', drain)
    owner.controls.steer('first')
    owner.controls.follow_up('next')
    assert [item.text for item in service.drain_steering(first)] == ['first']
    assert observed == [run] and service.require(second)._runtime.run is None
    assert owner.controls.request_scope_stop(run_id=run.run_id) == ['next']
    assert service.is_stop_requested(first) and not service.is_stop_requested(second)
    assert owner.lifecycle._cleanup_run(run) is False
    assert owner.is_busy() and owner._runtime.run is run and not run.controls_open
    owner.lifecycle.release_run(run)
    assert not owner.is_busy() and owner._runtime.run is None


def test_stale_same_id_run_cannot_mutate_current_collaborators():
    service = make_service()
    sid = service.create_session('A')
    owner, old = service._admit(sid, 'same-id', 'message')
    owner.controls.close_control_window(old)
    owner.lifecycle.release_run(old)
    _, current = service._admit(sid, 'same-id', 'message')
    for operation in (
        lambda: owner.controls.close_control_window(old),
        lambda: owner.lifecycle.release_run(old),
        lambda: owner.approvals.replace_pending_approval(old, {'batch_id': 'stale'}),
    ):
        with pytest.raises(SessionBusyError, match='different run'):
            operation()
    assert owner._runtime.run is current and current.controls_open
    assert owner._runtime.state.pending_tool_approval is None
    owner.controls.close_control_window(current)
    owner.lifecycle.release_run(current)
