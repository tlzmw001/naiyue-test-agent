"""Direct application lifecycle, not a second generator IO interpreter."""

import ast
import asyncio
import inspect
from pathlib import Path

import pytest


@pytest.mark.parametrize('module_name, names', [
    ('sessions.session_service', ('arun', 'acompact', 'aresume_tool_approval')),
    ('sessions.session_execution', ('arun', 'aresume_tool_approval', '_areview_partial_state',
                           '_prepare_resources', '_on_failure', '_afinish_run')),
    ('subagents.session_subagents', ('ainvoke_subagent', '_subagent_steps', 'settle_subagent_drivers')),
    ('sessions.session_persistence', ('afinish', 'arecover', 'arestore', 'ahistory', '_read_history', '_recover_checkpoint')),
])
def test_lifecycle_is_direct_async_with_no_generator_transport(module_name, names):
    from importlib import import_module
    module = import_module('naiyue_test_agent.application.' + module_name)
    owner = getattr(module, {'sessions.session_service': 'SessionService',
                            'sessions.session_execution': 'RunLifecycle',
                            'subagents.session_subagents': 'SubagentRegistry',
                            'sessions.session_persistence': 'SessionPersistence'}.get(module_name, ''), module)
    for name in names:
        assert inspect.iscoroutinefunction(getattr(owner, name))
    tree = ast.parse(Path(module.__file__).read_text())
    # The service now also owns its synchronous deletion_guard context manager.
    # Every async lifecycle body must still be direct, never a generator transport.
    for function in ast.walk(tree):
        if isinstance(function, ast.AsyncFunctionDef):
            assert not any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in ast.walk(function))
    guards = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
              and node.name in ('deletion_guard','manage_environment')]
    expected_yields = set()
    if module_name == 'sessions.session_service':
        assert {guard.name for guard in guards} == {'deletion_guard','manage_environment'}
        for guard in guards:
            assert any(isinstance(item,ast.Name) and item.id=='contextmanager' for item in guard.decorator_list)
            assert sum(isinstance(node,ast.Yield) for node in ast.walk(guard))==1
        expected_yields={node for guard in guards for node in ast.walk(guard) if isinstance(node,ast.Yield)}
    assert {node for node in ast.walk(tree) if isinstance(node, (ast.Yield, ast.YieldFrom))} == expected_yields
    assert not any(isinstance(node, ast.Name) and node.id in ('IOCall', 'arun_steps') for node in ast.walk(tree))


def test_blocking_calls_preserve_result_exception_identity_and_worker_execution():
    import threading
    from naiyue_test_agent.agent_runtime.async_utils import run_blocking

    async def scenario():
        owner = threading.get_ident()
        worker, value = await run_blocking(lambda: (threading.get_ident(), 42))
        assert worker != owner and value == 42
        failure = OSError('synthetic blocking failure')
        def fail():
            raise failure
        with pytest.raises(OSError) as caught:
            await run_blocking(fail)
        assert caught.value is failure
    asyncio.run(scenario())


def test_saved_configuration_is_pure_and_the_old_async_wrapper_is_removed():
    from naiyue_test_agent.application.sessions.session_recovery import prepare_saved_configuration
    from naiyue_test_agent.application.sessions.session_persistence import SessionPersistence
    assert inspect.isfunction(prepare_saved_configuration) and not inspect.iscoroutinefunction(prepare_saved_configuration)
    assert not hasattr(SessionPersistence,'_aprepare_saved_configuration')
