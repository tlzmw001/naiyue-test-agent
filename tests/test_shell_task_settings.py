"""Configured Shell deadlines reach the runner without changing other tools."""

from dataclasses import replace
from runtime_context_helpers import ToolAuthorization
from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
import asyncio
from types import SimpleNamespace

import pytest

from naiyue_test_agent.settings import AppSettings, load_settings
from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.tools.permissions import execution_environment
from naiyue_test_agent.tools.processes import ProcessOutcome


def test_execution_limit_defaults_to_unlimited(tmp_path):
    settings = load_settings(tmp_path / 'missing.toml')
    assert settings.shell_tasks.max_execution_seconds == 0
    assert 'tool_output' not in settings.model_dump()


@pytest.mark.parametrize('entry', ['-1', '1.5', 'true', '"120"', 'inf'])
def test_invalid_execution_limit_prevents_startup(tmp_path, caplog, entry):
    path = tmp_path / 'settings.toml'
    path.write_text('[shell_tasks]\nmax_execution_seconds=' + entry)
    with pytest.raises(ValueError, match='Shell task settings are invalid'):
        load_settings(path)
    assert 'shell_task_settings_invalid' in caplog.text


@pytest.mark.parametrize('entry', ['typo=120', 'foreground_wait_typo=120'])
def test_unused_or_unknown_shell_task_settings_rejected(tmp_path, entry):
    path = tmp_path / 'settings.toml'
    path.write_text('[shell_tasks]\n' + entry)
    with pytest.raises(ValueError, match='Shell task settings are invalid'):
        load_settings(path)


@pytest.mark.parametrize('entry', ['0', '-1', '1.5', 'true', '"120"'])
def test_foreground_wait_requires_positive_integer(tmp_path, entry):
    path = tmp_path / 'settings.toml'
    path.write_text('[shell_tasks]\nforeground_wait_seconds=' + entry)
    with pytest.raises(ValueError, match='Shell task settings are invalid'):
        load_settings(path)


def test_foreground_wait_default_and_configured_value(tmp_path):
    assert AppSettings().shell_tasks.foreground_wait_seconds == 120
    path = tmp_path / 'settings.toml'
    path.write_text('[shell_tasks]\nforeground_wait_seconds=9')
    assert load_settings(path).shell_tasks.foreground_wait_seconds == 9


@pytest.mark.parametrize('entry', ['unexpected=9', 'max_workspace_archive_bytes=9\nunexpected=1'])
def test_legacy_output_section_does_not_hide_unknown_keys(tmp_path, entry):
    path = tmp_path / 'settings.toml'
    path.write_text('[tool_output]\n' + entry)
    with pytest.raises(ValueError, match='Unknown tool output settings'):
        load_settings(path)


def test_legacy_output_section_requires_table(tmp_path):
    path = tmp_path / 'settings.toml'
    path.write_text('tool_output=1')
    with pytest.raises(ValueError, match='Unknown tool output settings'):
        load_settings(path)


def runtime_for(spec, args, root):
    context = typed_context(identity=RunIdentity(session_id='s', execution_root=ExecutionRoot(root) if root is not None else None), archive_root=root.parent / (root.name + '-archive'))
    subject = spec.permissions.prepare(args, context)
    subject.update(args=args, tool_name=spec.tool.name, path_field='path',
                   execution_environment=execution_environment())
    context = replace(context, authorization=ToolAuthorization(permit={'call_id': 'c', 'session_id': 's', 'agent_path': 'root', 'batch_id': 'b', 'tool_name': spec.tool.name, 'subject': subject}))
    return SimpleNamespace(context=context, tool_call_id='c', stream_writer=lambda _: None)


@pytest.mark.parametrize('name', ['bash', 'powershell'])
@pytest.mark.parametrize('cap', [0, 120])
@pytest.mark.parametrize('timeout', ['omitted', None, 0, 1, 60, 600])
def test_effective_deadline_reaches_real_process_request(monkeypatch, tmp_path, name, cap, timeout):
    from naiyue_test_agent.tools import shell
    requests = []
    monkeypatch.setattr(shell.ShellAdapter, 'resolve_executable', lambda *_: '/test/interpreter')
    monkeypatch.setattr(shell.ShellAdapter, 'probe_version', lambda *_: '7.4.0')
    async def run(self, request, consume, control=None):
        requests.append(request)
        await consume('stdout', b'done\n')
        return ProcessOutcome(0, 'exited', True, True, True, True, 0)
    monkeypatch.setattr(shell.HostProcessRunner, 'run', run)
    path = tmp_path / 'settings.toml'
    path.write_text(f'[shell_tasks]\nmax_execution_seconds={cap}\n')
    settings = load_settings(path)
    spec = next(item for item in create_builtin_tools(settings.tools,
        shell_task_settings=settings.shell_tasks) if item.tool.name == name)
    args = {'command': 'test'}
    if timeout != 'omitted':
        args['timeout'] = timeout
    values = spec.validate_input(args)
    arguments = {key: getattr(values, key) for key in spec.input_schema.model_fields}
    result = asyncio.run(spec.tool.coroutine(**arguments, runtime=runtime_for(spec, arguments, tmp_path)))
    positive = [value for value in (cap, arguments['timeout']) if value]
    assert [request.timeout_seconds for request in requests] == [min(positive) if positive else None]
    assert requests[0].cwd == tmp_path and requests[0].merge_output
    assert 'exit_code=0' in result and 'done' in result
    description = spec.input_schema.model_json_schema()['properties']['timeout']['description']
    assert 'configured' in description and 'seconds' in description
    assert 'workspace archive' not in spec.tool.description


@pytest.mark.parametrize('name,args', [('find', {'pattern': '*.py'}),
                                     ('grep', {'pattern': 'test'})])
def test_shell_execution_cap_does_not_change_search_tools(monkeypatch, tmp_path, name, args):
    from naiyue_test_agent.tools import processes
    from naiyue_test_agent.tools import find, grep
    requests = []
    monkeypatch.setattr(find, 'resolve_tool_binary', lambda _: '/test/fd')
    monkeypatch.setattr(grep, 'resolve_tool_binary', lambda _: '/test/rg')
    (tmp_path / 'target.py').write_text('test')
    async def run(self, request, consume, control=None):
        requests.append(request)
        return ProcessOutcome(0, 'exited', True, True, True, True, 0)
    monkeypatch.setattr(processes.HostProcessRunner, 'run', run)
    settings = AppSettings.model_validate({'shell_tasks': {'max_execution_seconds': 1}})
    spec = next(item for item in create_builtin_tools(settings.tools,
        shell_task_settings=settings.shell_tasks) if item.tool.name == name)
    requests.clear()  # Interpreter probes are outside the search call.
    values = spec.validate_input(args)
    arguments = {key: getattr(values, key) for key in spec.input_schema.model_fields}
    asyncio.run(spec.tool.coroutine(**arguments, runtime=runtime_for(spec, arguments, tmp_path)))
    assert len(requests) == (1 if name == 'find' else 2)
    assert all(request.timeout_seconds is None for request in requests)


def test_sync_shell_entry_applies_configured_cap(monkeypatch, tmp_path):
    from naiyue_test_agent.shell_task_settings import ShellTaskSettings
    from naiyue_test_agent.tools.bash import create_session_bash_tool
    from naiyue_test_agent.tools import shell
    requests = []
    async def execute(request, *_args, **_kwargs):
        requests.append(request)
        return 'done'
    monkeypatch.setattr(shell.ShellAdapter, 'resolve_executable', lambda *_: '/test/bash')
    monkeypatch.setattr(shell.ShellAdapter, 'probe_version', lambda *_: '5.2.0')
    monkeypatch.setattr(shell, 'execute_process_request', execute)
    spec = create_session_bash_tool(shell_task_settings=ShellTaskSettings(max_execution_seconds=90))
    args = {'command': 'test', 'timeout': 0}
    assert spec.tool.func(**args, runtime=runtime_for(spec, args, tmp_path)) == 'done'
    assert requests[0].timeout_seconds == 90


@pytest.mark.parametrize('timeout', [None, 0, 600])
def test_configured_cap_terminates_real_shell_and_settles_output(tmp_path, timeout):
    from langchain_core.tools import ToolException
    from naiyue_test_agent.shell_task_settings import ShellTaskSettings
    from naiyue_test_agent.tools.bash import create_session_bash_tool
    spec = create_session_bash_tool(shell_task_settings=ShellTaskSettings(max_execution_seconds=1))
    args = {'command': 'printf EARLY; sleep 60', 'timeout': timeout}
    async def scenario():
        with pytest.raises(ToolException, match='timed out') as caught:
            await asyncio.wait_for(spec.tool.coroutine(**args,
                runtime=runtime_for(spec, args, tmp_path)), 10)
        assert 'EARLY' in str(caught.value)
        assert 'output_complete=' in str(caught.value)
    asyncio.run(scenario())


def test_cli_loads_shell_policy_into_registered_tools(monkeypatch, tmp_path):
    from naiyue_test_agent import application
    from naiyue_test_agent.application import cli
    from naiyue_test_agent import settings as module
    from naiyue_test_agent.model_access import ModelCatalog
    from test_async_application import StreamingToolModel
    (tmp_path / 'settings.toml').write_text('[shell_tasks]\nmax_execution_seconds=731')
    monkeypatch.setattr(module, 'get_agent_dir', lambda: tmp_path)
    model = StreamingToolModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    monkeypatch.setattr('builtins.input', lambda _prompt='': ':quit')
    original, descriptions = cli.build_local_application, []
    def build(*args, **kwargs):
        app = original(*args, **kwargs)
        descriptions.extend(spec.tool.description for spec in app.get_registered_tools()
                            if spec.tool.name in ('bash', 'powershell'))
        return app
    monkeypatch.setattr(cli, 'build_local_application', build)
    assert cli.main(['--no-session', '--workspace', str(tmp_path)]) == 0
    assert len(descriptions) == 2
    assert all('limit: 731 seconds' in description for description in descriptions)
    assert model.seen == []


@pytest.mark.parametrize('preloaded', [False, True])
def test_web_loads_shell_policy_into_registered_tools(monkeypatch, tmp_path, preloaded):
    from naiyue_test_agent import settings as module
    from test_web_api import web_client
    from test_async_application import StreamingToolModel
    path = tmp_path / 'settings.toml'
    path.write_text('[shell_tasks]\nmax_execution_seconds=831')
    monkeypatch.setattr(module, 'get_agent_dir', lambda: tmp_path)
    model = StreamingToolModel(replies=[])
    async def scenario():
        options = {'runtime_settings': load_settings(path)} if preloaded else {}
        async with web_client(monkeypatch, tmp_path, model, **options) as (_client, web):
            specs = web.state.service.application.get_registered_tools()
            descriptions = [spec.tool.description for spec in specs if spec.tool.name in ('bash', 'powershell')]
            assert len(descriptions) == 2
            assert all('limit: 831 seconds' in description for description in descriptions)
            assert model.seen == []
    asyncio.run(scenario())
