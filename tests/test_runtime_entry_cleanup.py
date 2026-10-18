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


def test_current_assembly_requires_port_with_one_natural_end_boundary():
    from naiyue_test_agent.agent_runtime import _assemble_interactive_runtime
    parameters = inspect.signature(_assemble_interactive_runtime).parameters
    assert 'finish_pending_messages' not in parameters
    assert parameters['session_port'].default is inspect.Parameter.empty
    for name in ('is_stop_requested', 'finish_run_boundary', 'prepare_control_messages',
                 'resolve_model', 'resolve_permission_context', 'resolve_active_tool_names',
                 'drain_steering', 'resolve_prompt_parts', 'resolve_subagent_roles',
                 'resolve_project_instructions'):
        assert name not in parameters
    from naiyue_test_agent.agent_runtime.session_port import SessionPort
    assert callable(SessionPort.finish_run_boundary)
    assert not hasattr(SessionPort, 'finish_pending_messages')


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


def test_configured_probe_uses_current_async_graph_without_network(monkeypatch, capsys):
    from probe_budget_helpers import guard_fake_catalog
    monkeypatch.setattr('scripts.provider_acceptance_budget.guard_catalog', guard_fake_catalog)
    import sys
    from langchain_core.messages import AIMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from scripts import probe_configured_tool_loop as probe
    from test_agent_runtime import ToolCapableFake

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{'name': 'read_probe_value', 'args': {}, 'id': 'probe'}]),
        AIMessage(content='offline-value'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _: ModelCatalog('fake', {'fake': model}))
    monkeypatch.setattr(probe.secrets, 'token_hex', lambda _: 'offline-value')
    monkeypatch.setattr(sys, 'argv',
                        ['probe', '--config', 'synthetic.json', '--model-id', 'fake'])
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    # The test controls all IO; do not install a process-wide signal timer.
    monkeypatch.setattr(probe.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(probe.signal, 'alarm', lambda *_: 0)
    assert asyncio.run(probe.main()) == 0
    assert capsys.readouterr().out == (
        'status=PASS model_calls=2 tool_executions=1 final_matches_tool_result=true\n')


def test_configured_factory_and_selection_callback_are_retired():
    from naiyue_test_agent import agent_runtime
    from naiyue_test_agent.agent_runtime.call_observation import _ModelSelectionMiddleware
    assert not hasattr(agent_runtime, 'build_configured_runtime_agent')
    for function in (agent_runtime.build_runtime_agent, agent_runtime._assemble_interactive_runtime,
                     agent_runtime._build_runtime_agent, agent_runtime.make_model_selection_middleware,
                     _ModelSelectionMiddleware):
        assert 'record_selection' not in inspect.signature(function).parameters
