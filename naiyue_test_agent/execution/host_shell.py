"""Host interpreter discovery, environment and literal command construction."""
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import base64

from naiyue_test_agent.tools.processes import ProcessRequest, run_async_sync
from naiyue_test_agent.tools.permissions import execution_environment

logger = logging.getLogger(__name__)


def build_environment(settings, *, parent=None, platform=None):
    platform = platform or sys.platform
    windows = platform == 'win32'
    env = dict(os.environ if parent is None else parent)
    def put(name, value):
        if windows:
            for previous in list(env):
                if previous.casefold() == name.casefold():
                    del env[previous]
        env[name] = value
    # Collapse inherited Windows aliases too, without modifying the parent.
    if windows:
        original, env = env, {}
        for name, value in original.items():
            put(name, value)
    for name, value in settings.values.items():
        put(name, value)
    if settings.path_prepend:
        path_name = next((key for key in env if key.casefold() == 'path'), 'PATH') if windows else 'PATH'
        parts = [*settings.path_prepend]
        if env.get(path_name):
            parts.append(env[path_name])
        put(path_name, (';' if windows else os.pathsep).join(parts))
    for name in settings.unset_env:
        for key in list(env):
            if key.casefold() == name.casefold() if windows else key == name:
                del env[key]
    return env


class ShellAdapter:
    name = ''
    label = ''

    def resolve_executable(self, settings, *, platform=None):
        if settings.executable is not None:
            return shutil.which(settings.executable)
        return self.discover(platform or sys.platform)

    def probe_version(self, executable, settings, runner, root):
        data = bytearray()
        async def consume(_stream, chunk):
            remaining = settings.max_output_bytes - len(data)
            data.extend(chunk[:max(0, remaining)])
        request = ProcessRequest(self.probe_argv(executable), root,
            build_environment(settings.environment), settings.probe_timeout_seconds,
            merge_output=True, eof_policy='shell', execution_environment=execution_environment())
        try:
            outcome = run_async_sync(lambda: runner.run(request, consume, None))
        except OSError as error:
            logger.warning('shell_version_unavailable shell=%s error_type=%s', self.name, type(error).__name__)
            return 'unknown'
        match = re.search(rb'\b(\d+\.\d+(?:\.\d+){0,2})\b', data)
        if outcome.termination == 'exited' and outcome.returncode == 0 and match:
            return match[1].decode('ascii')
        logger.warning('shell_version_unavailable shell=%s', self.name)
        return 'unknown'

    def build_request(self, executable, version, command, timeout, root, settings):
        return ProcessRequest(self.command_argv(executable, version, command, settings), root,
            build_environment(settings.environment), timeout or None,
            merge_output=True, eof_policy='shell', execution_environment=execution_environment())


class BashAdapter(ShellAdapter):
    name = 'bash'
    label = 'Bash'

    def discover(self, platform):
        if platform != 'win32':
            return shutil.which('bash')
        # Git Bash is native; never silently select the WSL System32 shim.
        local = os.environ.get('LOCALAPPDATA')
        for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'),
                     str(Path(local) / 'Programs') if local else None):
            if base:
                for relative in ('Git/bin/bash.exe', 'Git/usr/bin/bash.exe'):
                    found = shutil.which(str(Path(base) / relative))
                    if found:
                        return found
        found = shutil.which('bash.exe')
        if found and any(part.lower() == 'git' for part in Path(found).parts):
            return found
        return None

    def probe_argv(self, executable):
        return [executable, '--version']

    def command_argv(self, executable, version, command, settings):
        return [executable, '--noprofile', '--norc', '-c', command]



class PowerShellAdapter(ShellAdapter):
    name = 'powershell'
    label = 'PowerShell'

    def discover(self, platform):
        return shutil.which('pwsh') or (shutil.which('powershell.exe') if platform == 'win32' else None)

    def probe_argv(self, executable):
        return [executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                '$PSVersionTable.PSVersion.ToString()']

    def command_argv(self, executable, version, command, settings):
        # Windows PowerShell 5.1 otherwise defaults text redirection to UTF-16.
        # Keep user-specified -Encoding authoritative. Do not touch PS7 native
        # binary redirection, ErrorActionPreference or LASTEXITCODE semantics.
        prefix = ('$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding = '
                  '[System.Text.UTF8Encoding]::new($false);\n')
        if version.startswith('5.'):
            prefix += ("$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8';\n"
                       "$PSDefaultParameterValues['Set-Content:Encoding'] = 'utf8';\n"
                       "$PSDefaultParameterValues['Add-Content:Encoding'] = 'utf8';\n")
        script = base64.b64encode((prefix + command).encode('utf-16-le')).decode('ascii')
        argv = [executable, '-NoLogo', '-NoProfile', '-NonInteractive']
        if settings.execution_policy == 'bypass':
            argv.extend(['-ExecutionPolicy', 'Bypass'])
        return [*argv, '-EncodedCommand', script]
