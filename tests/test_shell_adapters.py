"""Interpreter adapters preserve language, startup and host platform contracts."""

from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from native_application_helpers import async_test

import base64
import os
import shlex
import sys

import pytest
from langchain_core.tools import ToolException

from tool_feedback_helpers import invoke_tool_feedback


@async_test
async def test_bash_uses_no_profiles_and_inherits_environment_per_call(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import BashSettings, EnvironmentSettings, create_session_bash_tool
    startup = tmp_path / 'startup.sh'
    startup.write_text('printf WRONG_STARTUP\n')
    monkeypatch.setenv('BASH_ENV', str(startup))
    monkeypatch.setenv('TEST_PARENT', 'first')
    monkeypatch.setenv('NAIYUE_WEB_TOKEN', 'synthetic-private')
    tool = create_session_bash_tool(settings=BashSettings(environment=EnvironmentSettings(
        values={'TEST_OVERRIDE': 'new'}, unset_env=['BASH_ENV', 'NAIYUE_WEB_TOKEN'])))
    context = typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))
    command = 'printf "%s:%s:%s" "$TEST_PARENT" "$TEST_OVERRIDE" "$NAIYUE_WEB_TOKEN"; export ONLY_HERE=yes; cd /'
    first = (await invoke_tool_feedback(tool, {'command': command}, context=context, call_id='first', permission_mode='BypassApprovals'))
    assert first.status == 'success' and 'first:new:' in first.content
    assert 'WRONG_STARTUP' not in first.content and 'synthetic-private' not in first.content
    monkeypatch.setenv('TEST_PARENT', 'second')
    second = (await invoke_tool_feedback(tool, {'command': 'printf "%s:%s:" "$TEST_PARENT" "$ONLY_HERE"; pwd'}, context=context, call_id='second', permission_mode='BypassApprovals'))
    assert 'second::' + str(tmp_path.resolve()) in second.content
    assert os.environ['TEST_PARENT'] == 'second' and 'ONLY_HERE' not in os.environ


@async_test
async def test_bash_combines_stdout_stderr_before_tail_capture(tmp_path):
    from naiyue_test_agent.tools import BashSettings, create_session_bash_tool
    script = 'import os;os.write(1,b"out1\\n");os.write(2,b"err2\\n");os.write(1,b"out3\\n")'
    command = f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}'
    tool = create_session_bash_tool(settings=BashSettings(max_output_lines=2))
    result = (await invoke_tool_feedback(tool, {'command': command}, context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None)), call_id='merged', permission_mode='BypassApprovals'))
    assert result.status == 'success' and 'output:\nerr2\nout3\n' in result.content
    assert 'returned_lines=2-3' in result.content and 'truncated=true' in result.content
    assert 'raw_path=' in result.content and 'out1\n' not in result.content


@pytest.mark.parametrize('version', ['5.1.19041.1', '7.4.6'])
def test_powershell_encoded_unicode_and_version_specific_text_defaults(version, tmp_path):
    from naiyue_test_agent.execution.host_shell import PowerShellAdapter
    from naiyue_test_agent.tools import PowerShellSettings
    adapter = PowerShellAdapter()
    command = 'Write-Output "中文"; Set-Content file -Value text -Encoding Unicode'
    request = adapter.build_request('trusted-pwsh', version, command, 3, tmp_path, PowerShellSettings())
    assert request.argv[:4] == ['trusted-pwsh', '-NoLogo', '-NoProfile', '-NonInteractive']
    assert request.argv[-2] == '-EncodedCommand'
    script = base64.b64decode(request.argv[-1]).decode('utf-16-le')
    assert script.endswith(command) and '[System.Text.UTF8Encoding]::new($false)' in script
    assert ('Out-File:Encoding' in script) == version.startswith('5.')
    assert 'Set-ExecutionPolicy' not in script and 'ErrorActionPreference' not in script
    assert 'exit $LASTEXITCODE' not in script and '-ExecutionPolicy' not in request.argv
    assert request.timeout_seconds == 3 and request.merge_output and request.eof_policy == 'shell'


def test_bypass_is_only_a_child_argument():
    from naiyue_test_agent.execution.host_shell import PowerShellAdapter
    from naiyue_test_agent.tools import PowerShellSettings
    argv = PowerShellAdapter().command_argv('pwsh', '7.4.1', 'exit 3', PowerShellSettings(execution_policy='bypass'))
    assert argv[4:6] == ['-ExecutionPolicy', 'Bypass']
    assert base64.b64decode(argv[-1]).decode('utf-16-le').endswith('exit 3')


@async_test
async def test_missing_explicit_executable_has_no_fallback_but_tool_is_registered(tmp_path, monkeypatch):
    from naiyue_test_agent.tools import PowerShellSettings, create_session_powershell_tool
    from naiyue_test_agent.execution.host_shell import PowerShellAdapter
    def forbidden(*args):
        raise AssertionError('Explicit unavailable executable must not auto-discover')
    monkeypatch.setattr(PowerShellAdapter, 'discover', forbidden)
    spec = create_session_powershell_tool(settings=PowerShellSettings(executable=str(tmp_path / 'missing')))
    assert spec.tool.name == 'powershell' and spec.tool.metadata is None
    assert 'Interpreter availability is checked in that environment' in spec.tool.description
    result = (await invoke_tool_feedback(spec, {'command': 'Write-Output ok'}, context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None)), call_id='missing', permission_mode='BypassApprovals'))
    assert result.status == 'error' and 'interpreter is unavailable' in result.content


def test_windows_discovery_excludes_wsl_shim_and_prefers_pwsh(monkeypatch):
    from naiyue_test_agent.execution.host_shell import BashAdapter
    from naiyue_test_agent.execution.host_shell import PowerShellAdapter
    import shutil
    def which(name):
        return {'bash.exe': 'C:/Windows/System32/bash.exe', 'pwsh': 'C:/pwsh.exe',
                'powershell.exe': 'C:/Windows/powershell.exe'}.get(name)
    monkeypatch.setattr(shutil, 'which', which)
    assert BashAdapter().discover('win32') is None
    assert PowerShellAdapter().discover('win32') == 'C:/pwsh.exe'


def test_real_version_probe_is_bounded_and_failure_is_unknown(monkeypatch, tmp_path):
    from naiyue_test_agent.execution.host_shell import BashAdapter
    from naiyue_test_agent.tools import BashSettings
    from naiyue_test_agent.tools.processes import ProcessOutcome
    seen = []
    class Runner:
        async def run(self, request, consume, control):
            seen.append(request)
            await consume('stdout', b'GNU bash, version 5.2.15(1)-release')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
    adapter = BashAdapter()
    assert adapter.probe_version('/trusted/bash', BashSettings(probe_timeout_seconds=2), Runner(), tmp_path) == '5.2.15'
    assert seen[0].argv == ['/trusted/bash', '--version'] and seen[0].timeout_seconds == 2
    assert seen[0].cwd == tmp_path
    assert adapter.probe_version('/trusted/bash', BashSettings(max_output_bytes=4), Runner(), tmp_path) == 'unknown'


def test_windows_bash_discovery_never_synthesizes_a_relative_startup_path(monkeypatch):
    from naiyue_test_agent.execution.host_shell import BashAdapter
    import shutil
    for key in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        monkeypatch.delenv(key, raising=False)
    seen = []
    def which(name):
        seen.append(name)
        return None
    monkeypatch.setattr(shutil, 'which', which)
    assert BashAdapter().discover('win32') is None
    assert seen == ['bash.exe']
