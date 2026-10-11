"""Retired entry points stay removed while current lifecycle guards remain."""

import asyncio
import importlib
import importlib.util
import inspect

import pytest

from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services
from test_async_application import StreamingToolModel, make_app


@pytest.mark.parametrize('module,name', [
    ('events', 'bind_run_event_publisher'), ('events', 'current_run_event_publisher'),
    ('turn_observation', 'bind_turn_observer'), ('turn_observation', 'current_turn_observer'),
    ('control_observation', 'bind_control_observer'), ('control_observation', 'current_control_observer'),
    ('context_operation_observation', 'bind_context_operation_observer'),
    ('context_operation_observation', 'current_context_operation_observer'),
    ('run_control', 'bind_run_control'), ('run_control', 'current_run_control'),
    ('errors', 'bind_failure_policy'), ('errors', 'current_failure_policy'),
    ('run_services', 'bind_service'), ('run_services', 'current_service'),
    ('call_observation', 'failure_data'),
    ('', 'build_steerable_runtime_agent'), ('', 'build_interactive_runtime_agent'),
    ('', 'invoke_agent_run_in_workspace'), ('', 'resume_agent_run_in_workspace'),
    ('', 'resume_agent_run_with_grants'),
])
def test_retired_runtime_entry_is_not_available(module, name):
    target = importlib.import_module('naiyue_test_agent.agent_runtime' + ('.' + module if module else ''))
    assert not hasattr(target, name), f'Retired runtime entry remains: {module}.{name}'


@pytest.mark.parametrize('name', ['finish_steering', 'finish_pending_messages'])
def test_retired_session_boundary_is_not_available(name):
    from naiyue_test_agent.application import SessionService
    assert not hasattr(SessionService, name)


def test_retired_control_event_forwarding_module_is_not_available():
    assert importlib.util.find_spec('naiyue_test_agent.agent_runtime.control_events') is None


@pytest.mark.parametrize('name', ['ModelResponseRejected', 'RejectionReason'])
def test_model_layer_does_not_export_retired_runtime_error_alias(name):
    from naiyue_test_agent.model_access import errors
    assert not hasattr(errors, name)


def test_current_assembly_has_one_natural_end_control_callback():
    from naiyue_test_agent.agent_runtime import _assemble_interactive_runtime
    parameters = inspect.signature(_assemble_interactive_runtime).parameters
    assert 'finish_pending_messages' not in parameters
    for name in ('is_stop_requested', 'finish_run_boundary', 'prepare_control_messages'):
        assert parameters[name].default is inspect.Parameter.empty


def test_central_run_services_still_prevent_closing_from_active_run(monkeypatch):
    async def scenario():
        app = await make_app(monkeypatch, StreamingToolModel(replies=[]))
        services = RunServices(publisher=RunEventPublisher('s', 'r', ()))
        try:
            with bind_run_services(services):
                with pytest.raises(RuntimeError, match='active run'):
                    await app.aclose()
                assert not app._closing and not app._closed
        finally:
            await app.aclose()
        assert app._closed
    asyncio.run(scenario())
