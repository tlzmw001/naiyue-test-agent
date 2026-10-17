"""Native PowerShell commands; no syntax translation or machine policy changes."""

import base64
import shutil

from .settings import PowerShellSettings
from .shell import ShellAdapter, build_shell_tool


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


def create_session_powershell_tool(*, process_settings=None, settings=None, output_settings=None, progress_settings=None):
    return build_shell_tool(PowerShellAdapter(), settings or PowerShellSettings(), process_settings, output_settings, progress_settings)
