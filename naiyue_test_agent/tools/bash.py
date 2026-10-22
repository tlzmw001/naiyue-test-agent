"""Native Bash interpreter discovery and noninteractive command construction."""

import os
from pathlib import Path
import shutil

from .settings import BashSettings
from .shell import ShellAdapter, build_shell_tool


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


def create_session_bash_tool(*, process_settings=None, settings=None, shell_task_settings=None, progress_settings=None):
    return build_shell_tool(BashAdapter(), settings or BashSettings(), process_settings, shell_task_settings, progress_settings)
