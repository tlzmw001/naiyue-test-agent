"""Common approved Shell execution; adapters supply only interpreter behavior."""

import logging
import os
from pathlib import Path
import re
import shutil
import sys

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field

from .permissions import ToolPermission, execution_environment, require_tool_permit
from .processes import HostProcessRunner, ProcessRequest, run_async_sync
from .registry import ToolSpec
from .schemas import ExecutionTimeout, build_tool_schemas
from .settings import ToolOutputSettings
from .process_execution import execute_process_request


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

    def probe_version(self, executable, settings, runner):
        data = bytearray()
        async def consume(_stream, chunk):
            remaining = settings.max_output_bytes - len(data)
            data.extend(chunk[:max(0, remaining)])
        request = ProcessRequest(self.probe_argv(executable), Path.cwd(),
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



class ShellInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    timeout: ExecutionTimeout = None


def build_shell_tool(adapter, settings, process_settings=None, output_settings=None, progress_settings=None):
    settings = settings.model_copy(deep=True)
    runner = HostProcessRunner(process_settings)
    executable = adapter.resolve_executable(settings)
    version = adapter.probe_version(executable, settings, runner) if executable else 'unavailable'
    if not executable:
        logger.warning('shell_interpreter_unavailable shell=%s', adapter.name)
    public, execution = build_tool_schemas(adapter.label + 'Input', ShellInput,
        command=(str, Field(min_length=1, max_length=settings.max_command_chars)))
    output_settings = output_settings or ToolOutputSettings()

    async def execute(command: str, runtime: ToolRuntime[dict, dict], timeout: int | None = None):
        root = runtime.context.get('workspace_root')
        if not isinstance(root, Path) or not root.is_absolute() or not root.is_dir():
            raise ToolException(f'{adapter.label} has no valid session workspace.')
        require_tool_permit(runtime)
        if not executable:
            raise ToolException(f'{adapter.label} interpreter is unavailable. Configure its executable or install it before registering the tool again.')
        request = adapter.build_request(executable, version, command, timeout, root, settings)
        return await execute_process_request(request, runtime, name=adapter.name, label=adapter.label,
            settings=settings, runner=runner, output_settings=output_settings, progress_settings=progress_settings)

    def execute_sync(command: str, runtime: ToolRuntime[dict, dict], timeout: int | None = None):
        return run_async_sync(lambda: execute(command, runtime, timeout))

    description = (f'Run an approved local {adapter.label} command with current user access. '
        f'Interpreter available={str(executable is not None).lower()}; version={version}. '
        f'The workspace is the working directory, not a sandbox. Each call starts a fresh '
        f'noninteractive shell with inherited, configured environment and no stdin/TTY. '
        'Execution has no default time limit. Omit timeout or use 0 for unlimited execution; '
        'a positive integer specifies seconds for this call. '
        f'Returns merged stdout/stderr tail up to {settings.max_output_lines} lines and '
        f'{settings.max_output_bytes} UTF-8 bytes; incomplete capture and partial lines are marked. '
        f'Long or normalized output is saved under naiyue-agent-output/ when possible; '
        f'use read on the supplied UTF-8 text path. Raw bytes and text views share '
        f'{settings.max_archive_bytes} bytes per call and '
        f'{output_settings.max_workspace_archive_bytes} bytes per workspace. '
        f'Nonzero exit or timeout returns an error; no automatic retry. Use {adapter.label} syntax.')
    item = StructuredTool.from_function(execute_sync, execute, name=adapter.name,
        description=description, args_schema=execution,
        metadata={'shell': {'available': executable is not None, 'version': version}})
    item.handle_validation_error = (f'Invalid {adapter.name} arguments. Provide a non-empty command '
        f'up to {settings.max_command_chars} characters and optional timeout as a nonnegative '
        'integer in seconds; omit, null, or 0 for no time limit.')
    return ToolSpec(item, public, execution_mode='sequential', side_effects='may_mutate',
                    permissions=ToolPermission(default='ask'))
