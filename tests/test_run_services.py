"""One scoped service object replaces independent ambient channels."""

from threading import Thread

import pytest

from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services, current_run_services
from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy
from naiyue_test_agent.agent_runtime.invocation_context import make_invocation_context
from naiyue_test_agent.agent_runtime.run_control import RunControl


def test_single_binding_builds_explicit_context_and_restores_parent():
    publisher = RunEventPublisher('s', 'r', ())
    control = RunControl()
    policy = RuntimeErrorPolicy()
    services = RunServices(publisher=publisher, control=control, errors=policy)
    assert current_run_services() is None
    with bind_run_services(services):
        assert current_run_services().publisher is publisher and current_run_services().control is control
        context = make_invocation_context('s', 'r', None, None, ())
        assert context['_run_event_publisher'] is publisher and context['_failure_policy'] is policy
        assert context['_run_control'] is control and '_failure_reporter' not in context
        assert '_run_control' not in make_invocation_context('other', 'r', None, None, ())
        with pytest.raises(ValueError):
            with bind_run_services(RunServices()):
                assert current_run_services() is not services
                raise ValueError('probe')
        assert current_run_services() is services
    assert current_run_services() is None


def test_new_thread_does_not_inherit_another_runs_services():
    values = []
    with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', ()))):
        thread = Thread(target=lambda: values.append(current_run_services()))
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert values == [None]


def test_session_contract_types_are_shared_without_application_imports():
    from naiyue_test_agent.contracts import session_events
    from naiyue_test_agent.contracts.failures import RunFailureData
    from naiyue_test_agent.agent_runtime.events import RunStartedData, RunFailureData as LegacyFailure
    from naiyue_test_agent.contracts.session_events import ControlData
    assert RunStartedData is session_events.RunStartedData
    assert ControlData is session_events.ControlData
    assert LegacyFailure is RunFailureData
