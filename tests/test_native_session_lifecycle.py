"""Direct application lifecycle, not a second generator IO interpreter."""

import ast
import asyncio
import inspect
from pathlib import Path

import pytest


@pytest.mark.parametrize('module_name, names', [
    ('session_service', ('arun', 'acompact', 'aresume_tool_approval')),
    ('session_execution', ('_arun', 'aresume_tool_approval', '_areview_partial_state',
                           '_prepare_resources', '_on_failure', '_afinish_run')),
    ('session_subagents', ('ainvoke_subagent', '_subagent_steps', 'settle_subagent_drivers')),
    ('session_persistence', ('afinish', 'arecover', 'arestore', 'ahistory', '_aprepare_saved_configuration', '_read_history', '_recover_checkpoint')),
])
def test_lifecycle_is_direct_async_with_no_generator_transport(module_name, names):
    from importlib import import_module
    module = import_module('naiyue_test_agent.application.' + module_name)
    owner = getattr(module, {'session_service': 'SessionService',
                            'session_execution': 'SessionExecution',
                            'session_subagents': 'AgentSessionSubagents',
                            'session_persistence': 'SessionPersistence'}.get(module_name, ''), module)
    for name in names:
        assert inspect.iscoroutinefunction(getattr(owner, name))
    tree = ast.parse(Path(module.__file__).read_text())
    assert not any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in ast.walk(tree))
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
