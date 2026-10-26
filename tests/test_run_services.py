"""One scoped service object replaces independent ambient channels."""

from runtime_context_helpers import RunIdentity, ExecutionRoot, typed_context, typed_scope
from threading import Thread

import pytest

from naiyue_test_agent.agent_runtime.run_scope import bind_run_scope, current_run_scope
from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy
from naiyue_test_agent.agent_runtime.invocation_context import make_invocation_context
from naiyue_test_agent.agent_runtime.run_control import RunControl


def test_single_binding_builds_explicit_context_and_restores_parent():
    publisher = RunEventPublisher('s', 'r', ())
    control = RunControl()
    policy = RuntimeErrorPolicy()
    services = typed_scope(publisher=publisher, control=control, errors=policy)
    assert current_run_scope() is None
    with bind_run_scope(services):
        assert current_run_scope().observation.publisher is publisher and current_run_scope().control is control
        context = make_invocation_context('s', 'r', None, None, ())
        assert context.scope.observation.publisher is publisher and context.scope.errors is policy
        assert context.scope.control is control and not hasattr(context, '_failure_reporter')
        with pytest.raises(ValueError, match='identity'):
            make_invocation_context('other', 'r', None, None, ())
        with pytest.raises(ValueError):
            with bind_run_scope(typed_scope()):
                assert current_run_scope() is not services
                raise ValueError('probe')
        assert current_run_scope() is services
    assert current_run_scope() is None


def test_new_thread_does_not_inherit_another_runs_services():
    values = []
    with bind_run_scope(typed_scope(publisher=RunEventPublisher('s', 'r', ()))):
        thread = Thread(target=lambda: values.append(current_run_scope()))
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert values == [None]


def test_background_notice_projection_is_root_and_exact_scope_only(tmp_path):
    from naiyue_test_agent.tools.background_tasks import BackgroundNoticeSnapshot
    notice = BackgroundNoticeSnapshot('s', tmp_path, 'r', (('a' * 32, 1),), 'status')
    identity = RunIdentity('s', 'r', execution_root=ExecutionRoot(tmp_path))
    with bind_run_scope(typed_scope(identity=identity, background_notices=notice)):
        assert make_invocation_context('s', 'r', tmp_path, None, ()).scope.background_notices is notice
        for sid, run, root, path in [('other', 'r', tmp_path, 'root'), ('s', 'other', tmp_path, 'root'),
                ('s', 'r', tmp_path / 'other', 'root'), ('s', 'r', tmp_path, 'root/tester/1')]:
            with pytest.raises(ValueError, match='identity'):
                make_invocation_context(sid, run, root, None, (), agent_path=path)
    with pytest.raises(ValueError, match='notice scope'):
        typed_scope(identity=RunIdentity('foreign', 'r'), background_notices=notice)


def test_session_contract_types_are_shared_without_application_imports():
    from naiyue_test_agent.contracts import session_events
    from naiyue_test_agent.contracts.failures import RunFailureData
    from naiyue_test_agent.contracts.session_events import ControlData
    events = []
    publisher = RunEventPublisher('session', 'run', (events.append,))
    started = session_events.RunStartedData('message')
    control = ControlData('control', 'stop')
    failure = RunFailureData('execute', 'unknown', 'unclassified', 'RuntimeError')
    finished = session_events.RunFinishedData('failed', failure=failure)
    for event_type, payload in [('run_started', started), ('control_accepted', control), ('run_finished', finished)]:
        publisher.publish(event_type, payload)
    assert [event.data for event in events] == [started, control, finished]
    assert events[-1].data.failure is failure


@pytest.mark.parametrize('session_id,run_id', [
    ('other', 'r'),
    ('s', 'other'),
], ids=['different-session', 'different-run'])
def test_context_projection_rejects_mismatched_run(session_id, run_id):
    services = typed_scope(publisher=RunEventPublisher('s', 'r', ()), errors=RuntimeErrorPolicy(), control=RunControl())
    with bind_run_scope(services):
        with pytest.raises(ValueError, match='identity'):
            make_invocation_context(session_id, run_id, None, None, ())
        assert current_run_scope() is services
