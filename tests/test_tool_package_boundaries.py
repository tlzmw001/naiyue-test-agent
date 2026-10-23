"""Concrete tools are independent; runtime owns execution and observation."""

from runtime_context_helpers import ExecutionRoot, RunIdentity, ToolAuthorization, typed_context
from naiyue_test_agent.application import prepare_tool_registry

import importlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def test_tools_import_without_loading_project_runtime_or_application():
    result = subprocess.run([
        sys.executable, '-c',
        "import importlib, sys; "
        "[importlib.import_module('naiyue_test_agent.tools.' + name) for name in "
        "['read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell', 'shell', 'output', 'permissions']]; "
        "assert not any(name.startswith(('naiyue_test_agent.agent_runtime', "
        "'naiyue_test_agent.application')) for name in sys.modules)",
    ], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_old_tool_execution_modules_are_removed():
    runtime = ROOT / 'naiyue_test_agent' / 'agent_runtime'
    for path in ['tools', 'tool_approval.py', 'file_permissions.py',
                 'tool_batch.py', 'async_tool_batch.py']:
        assert not (runtime / path).exists(), path


def test_tool_and_middleware_contracts_keep_their_names():
    from naiyue_test_agent import tools
    from naiyue_test_agent.agent_runtime import call_observation, _build_runtime_agent
    from naiyue_test_agent.agent_runtime.tool_execution.approval import ObservedHumanInTheLoopMiddleware
    from naiyue_test_agent.agent_runtime.tool_execution.batch import ToolBatchMiddleware
    from naiyue_test_agent.agent_runtime.tool_execution.observation import _record_tool_call

    for name in ['read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell']:
        item = getattr(tools, f'create_session_{name}_tool')()
        assert item.tool.name == name
        assert 'runtime' not in item.tool.tool_call_schema.model_json_schema()['properties']
    assert _record_tool_call.name == '_record_tool_call'
    assert ToolBatchMiddleware(prepare_tool_registry([])).name == 'ToolBatchMiddleware'
    assert ObservedHumanInTheLoopMiddleware(interrupt_on={}).name == 'ObservedHumanInTheLoopMiddleware'
    assert '_record_tool_call' not in vars(call_observation)
    assert 'ObservedHumanInTheLoopMiddleware' not in vars(call_observation)
    assert _build_runtime_agent.__globals__['_record_tool_call'] is _record_tool_call
    for name in ['observation', 'approval', 'batch', 'async_batch']:
        module = importlib.import_module(f'naiyue_test_agent.agent_runtime.tool_execution.{name}')
        assert module.__file__


def test_bash_passes_the_original_context_control_to_the_runner(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import bash as bash_module
    from naiyue_test_agent.tools.processes import HostProcessRunner
    import asyncio
    from naiyue_test_agent.tools.processes import ProcessOutcome
    from naiyue_test_agent.agent_runtime.run_control import RunControl

    control = RunControl()
    seen = []

    async def run(self, request, consume, control):
        if request.argv[-1] == '--version':
            await consume('stdout', b'GNU bash, version 5.2.0')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        seen.append((request.argv[-1], request.cwd, request.timeout_seconds, control))
        await consume('stdout', b'ok')
        return ProcessOutcome(0, 'exited', True, True, True, True, 0)

    monkeypatch.setattr(HostProcessRunner, 'run', run)
    from naiyue_test_agent.tools.permissions import execution_environment
    permit = {'call_id': 'call', 'session_id': 's', 'agent_path': 'root',
              'subject': {'workspace': str(tmp_path), 'execution_environment': execution_environment()}}
    runtime = SimpleNamespace(tool_call_id='call', context=typed_context(identity=RunIdentity(session_id='s', execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None), authorization=ToolAuthorization(permit=permit), archive_root=tmp_path.parent / (tmp_path.name + '-archive'), control=control))
    result = bash_module.create_session_bash_tool().tool.func('echo ok', runtime, timeout=7)
    assert seen == [('echo ok', tmp_path, 7, control)]
    assert seen[0][3] is control
    assert 'output:\nok' in result
