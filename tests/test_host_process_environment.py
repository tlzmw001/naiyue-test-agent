"""A path does not grant process execution; tools use the injected provider."""
import asyncio
from dataclasses import replace

import pytest

from naiyue_test_agent.contracts.run_context import RunIdentity, RunScope, RuntimeContext, RunCapabilities
from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.execution_roots import ExecutionRoot
from naiyue_test_agent.tools import create_session_bash_tool, create_aitest_kit_tool
from tool_feedback_helpers import invoke_tool_feedback


def test_path_without_process_capability_cannot_execute(tmp_path):
    async def scenario():
        context = RuntimeContext(RunScope(RunIdentity('S', execution_root=ExecutionRoot(tmp_path))))
        result = await invoke_tool_feedback(create_session_bash_tool(), {'command': 'printf MUST_NOT_EXECUTE'},
            call_id='shell', context=context, permission_mode='BypassApprovals')
        assert result.status == 'error'
        assert 'MUST_NOT_EXECUTE' not in result.content
    asyncio.run(scenario())


@pytest.mark.parametrize('name', ['bash', 'aitest_kit'])
def test_tool_factory_and_dispatch_use_provider_without_host_io(monkeypatch, name):
    import shutil
    from naiyue_test_agent.tools.processes import HostProcessRunner
    def forbidden(*args, **kwargs): raise AssertionError('Tool accessed host execution')
    monkeypatch.setattr(shutil, 'which', forbidden)
    monkeypatch.setattr(HostProcessRunner, '__init__', forbidden)
    calls = []
    class Processes:
        environment = EnvironmentRef('sandbox:B', 'sandbox', 'P', '/project', 'linux')
        owner_session_id = 'S'
        def validate(self): calls.append('validate')
        async def shell(self, *args, **kwargs): calls.append('shell'); return 'PROVIDER_SHELL'
        async def argv(self, *args, **kwargs): calls.append('argv'); return 'PROVIDER_ARGV'
    async def scenario():
        processes = Processes()
        scope = RunScope(RunIdentity('S', project_id='P'), capabilities=RunCapabilities(processes=processes),
                         environment=processes.environment)
        tool = create_session_bash_tool() if name == 'bash' else create_aitest_kit_tool()
        args = {'command': 'printf x'} if name == 'bash' else {'args': ['doctor']}
        result = await invoke_tool_feedback(tool, args, call_id='C', context=RuntimeContext(scope), permission_mode='BypassApprovals')
        assert result.status == 'success', result.content
        assert result.content == ('PROVIDER_SHELL' if name == 'bash' else 'PROVIDER_ARGV')
        assert 'validate' in calls and ('shell' if name == 'bash' else 'argv') in calls
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['owner', 'binding', 'files'])
def test_process_capability_mismatch_never_starts_command(tmp_path, change):
    from naiyue_test_agent.execution.host_files import HostFileSystem
    from naiyue_test_agent.execution.host_processes import HostProcesses
    files = HostFileSystem(ExecutionRoot(tmp_path), None, session_id='S')
    scope = RunScope(RunIdentity('S', execution_root=files.execution_root),
        capabilities=RunCapabilities(files=files, processes=HostProcesses(files)), environment=files.environment)
    if change == 'owner': scope = replace(scope, identity=replace(scope.identity, session_id='OTHER'))
    if change == 'binding': scope = replace(scope, environment=replace(scope.environment, binding_id='OTHER'))
    if change == 'files':
        other = replace(files.environment, binding_id='OTHER')
        from types import SimpleNamespace
        scope = replace(scope, capabilities=replace(scope.capabilities, files=SimpleNamespace(environment=other)))
    async def scenario():
        result = await invoke_tool_feedback(create_session_bash_tool(), {'command': 'printf BAD > forbidden'},
            call_id='C', context=RuntimeContext(scope), permission_mode='BypassApprovals')
        assert result.status == 'error'
        assert not (tmp_path / 'forbidden').exists()
    asyncio.run(scenario())


def test_host_interpreter_preparation_is_shared_and_does_not_block_loop(monkeypatch, tmp_path):
    from threading import Event
    from types import SimpleNamespace
    from naiyue_test_agent.execution import host_processes
    from naiyue_test_agent.execution.host_shell import ShellAdapter
    from runtime_context_helpers import typed_context
    from naiyue_test_agent.tools.settings import BashSettings
    from naiyue_test_agent.shell_task_settings import ShellTaskSettings
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release, probes, executed = asyncio.Event(), Event(), [], []
        monkeypatch.setattr(ShellAdapter, 'resolve_executable', lambda *_: 'owned-shell')
        def probe(*_):
            probes.append(True); loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return '5.2.0'
        async def execute(request, *_args, **_kwargs):
            executed.append(request)
            return 'done'
        monkeypatch.setattr(ShellAdapter, 'probe_version', probe)
        monkeypatch.setattr(host_processes, 'execute_process_request', execute)
        context = typed_context(identity=RunIdentity('S', execution_root=ExecutionRoot(tmp_path)))
        runtime = SimpleNamespace(context=context, tool_call_id='C')
        port = context.scope.capabilities.processes
        options = dict(settings=BashSettings(), process_settings=None, task_settings=ShellTaskSettings(), progress_settings=None)
        tasks = [asyncio.create_task(port.shell('bash', 'printf ok', None, False, runtime, **options)) for _ in range(2)]
        try:
            await asyncio.wait_for(entered.wait(), 5)
            heartbeat = asyncio.create_task(asyncio.sleep(0, result='responsive'))
            assert await asyncio.wait_for(heartbeat, 5) == 'responsive'
            assert not any(task.done() for task in tasks)
        finally:
            release.set()
            results = await asyncio.gather(*tasks)
        assert results == ['done', 'done'] and probes == [True]
        assert len(executed) == 2
        assert all(request.cwd == tmp_path and request.execution_environment == port.environment.public() for request in executed)
    asyncio.run(scenario())


def test_registered_child_and_restored_root_keep_process_environment(monkeypatch, tmp_path):
    import json
    from langchain_core.messages import AIMessage, ToolMessage
    from test_subagent_execution import ChildModel
    from test_async_application import call
    from test_multi_project_runtime import multi_app, projects
    from naiyue_test_agent.tools import SubAgentSpec, create_subagent_tool
    async def scenario():
        model = ChildModel(replies=[call('subagent', {'agent': 'worker', 'task': 'check cwd'}, 'delegate'),
            call('bash', {'command': 'pwd'}, 'child-shell'), AIMessage(content='child done'), AIMessage(content='root done')])
        async with multi_app(monkeypatch, tmp_path, model, tools=[create_subagent_tool(), create_session_bash_tool()],
                subagents=(SubAgentSpec('worker', 'Check cwd', 'Report', ('bash',)),),
                permissions={'allow': [{'tool': 'bash'}]}) as (app, _):
            (project, host), _ = await projects(app, tmp_path)
            sid = await host.sessions.create('A')
            result = await host.runs.start(sid, [('user', 'delegate')])
            outer = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert json.loads(outer.content)['report'] == 'child done'
            root = model.services[0]
            children = [scope for scope in model.services if scope.identity.agent_path != 'root']
            assert children and all(scope.capabilities.processes is root.capabilities.processes for scope in children)
            assert all(scope.environment == root.environment and scope.capabilities.processes.owner_session_id == sid for scope in model.services)
            assert str(project.source.path) in str(children[-1].identity.execution_path)
            await app.projects.close(project.project_id)
            host = await app.projects.open(project.project_id)
            await host.sessions.restore(sid)
            model.replies.extend([call('bash', {'command': 'pwd'}, 'restored'), AIMessage(content='again')])
            result = await host.runs.start(sid, [('user', 'again')])
            shell = [m for m in result['messages'] if isinstance(m, ToolMessage)][-1]
            assert shell.status == 'success' and str(project.source.path) in shell.content
            current = model.services[-1]
            assert current.environment == root.environment
            assert current.capabilities.processes is not root.capabilities.processes
    asyncio.run(scenario())


def test_valid_project_shell_survives_deleted_service_cwd(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    service_root, project_root = tmp_path / 'service', tmp_path / 'project'
    service_root.mkdir()
    project_root.mkdir()
    repository = Path(__file__).resolve().parents[1]
    script = '''
import asyncio, os, sys
from pathlib import Path
from types import SimpleNamespace
from runtime_context_helpers import typed_context, RunIdentity, ExecutionRoot
from naiyue_test_agent.tools.settings import BashSettings
from naiyue_test_agent.shell_task_settings import ShellTaskSettings
if os.name == 'nt':
    # Windows prevents deleting a process cwd; inject the same unavailable fact.
    def unavailable(cls):
        raise FileNotFoundError('Service cwd is unavailable')
    Path.cwd = classmethod(unavailable)
else:
    os.rmdir(Path.cwd())
context = typed_context(identity=RunIdentity('S', execution_root=ExecutionRoot(Path(sys.argv[1]))),
                        archive_root=Path(sys.argv[2]))
async def main():
    result = await context.scope.capabilities.processes.shell('bash', 'printf expected', None, False,
        SimpleNamespace(context=context, tool_call_id='C'), settings=BashSettings(),
        process_settings=None, task_settings=ShellTaskSettings(), progress_settings=None)
    assert 'expected' in result, result
asyncio.run(main())
'''
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(repository), str(repository / 'tests')))}
    completed = subprocess.run([sys.executable, '-c', script, str(project_root), str(tmp_path / 'archives')],
        cwd=service_root, env=environment, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
