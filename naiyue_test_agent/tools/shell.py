"""Shell schemas and approvals delegate to the run-owned process capability."""

from naiyue_test_agent.contracts.run_context import RuntimeContext
from dataclasses import dataclass

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from .permissions import ToolPermission, require_tool_permit, require_process_system
from .processes import run_async_sync
from .registry import ToolSpec
from .schemas import build_tool_schemas
from ..shell_task_settings import ShellTaskSettings


@dataclass(frozen=True)
class ShellPermission(ToolPermission):
    foreground_wait_seconds: int = 120

    def prepare(self, args, context):
        require_process_system(context).validate()
        subject = super().prepare(args, context)
        if args.get('background', False):
            subject['target'] = '[background] ' + subject['target']
        elif context.scope.identity.agent_path == 'root':
            subject['target'] = f'[automatic background after {self.foreground_wait_seconds}s; original execution deadline applies] ' + subject['target']
        return subject


class ShellInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    timeout: int | None = Field(default=None, ge=0, description=(
        'Optional execution timeout in seconds. Omit, null, or 0 uses the configured '
        'Shell execution limit (unlimited when not configured). A positive integer '
        'can shorten but cannot extend the configured limit.'))
    background: bool = Field(default=False, description=(
        'Run under application custody across conversation turns. Root agent only. '
        'Returns a task_id; inspect with task_list/task_output and stop with task_stop.'))


def build_shell_tool(name, settings, process_settings=None, shell_task_settings=None, progress_settings=None):
    settings = settings.model_copy(deep=True)
    label = 'Bash' if name == 'bash' else 'PowerShell'
    public, execution = build_tool_schemas(label + 'Input', ShellInput,
        command=(str, Field(min_length=1, max_length=settings.max_command_chars)))
    task_settings = shell_task_settings or ShellTaskSettings()

    async def execute(command: str, runtime: ToolRuntime[RuntimeContext], timeout: int | None = None, background: bool = False):
        require_tool_permit(runtime)
        port = require_process_system(runtime.context)
        return await port.shell(name, command, timeout, background, runtime, settings=settings,
            process_settings=process_settings, task_settings=task_settings, progress_settings=progress_settings)

    def execute_sync(command: str, runtime: ToolRuntime[RuntimeContext], timeout: int | None = None, background: bool = False):
        return run_async_sync(lambda: execute(command, runtime, timeout, background))

    description = (f'Run an approved {label} command in this session execution environment. '
        'Interpreter availability is checked in that environment. The selected workspace is cwd. Each call starts a fresh '
        f'noninteractive shell with inherited, configured environment and no stdin/TTY. '
        f'Configured Shell execution limit: {task_settings.max_execution_seconds} seconds '
        '(0 means unlimited). Omit timeout, null, or 0 to use that limit; '
        'a positive integer in seconds can shorten but cannot extend it. '
        'Root agents may explicitly set background=true to return a task_id and keep the command '
        'across turns. Use task_list/task_output/task_stop; no automatic model wakeup. '
        f'Root foreground calls still executing after {task_settings.foreground_wait_seconds} seconds '
        'automatically return a background task_id, keeping the original execution deadline. '
        'An earlier or equal execution deadline wins; child agents remain foreground. '
        f'Each background task archives up to {task_settings.max_background_archive_bytes} bytes; reaching '
        'that limit stops the task. Foreground calls continue when their archive limit is reached. '
        f'Returns merged stdout/stderr tail up to {settings.max_output_lines} lines and '
        f'{settings.max_output_bytes} UTF-8 bytes; incomplete capture and partial lines are marked. '
        f'Long or normalized output is saved under naiyue-agent-output/ when possible; '
        f'use read on the supplied UTF-8 text path. Raw bytes and text views share '
        f'{settings.max_archive_bytes} bytes per call. '
        f'Nonzero exit or timeout returns an error; no automatic retry. Use {label} syntax.')
    item = StructuredTool.from_function(execute_sync, execute, name=name,
        description=description, args_schema=execution)
    item.handle_validation_error = (f'Invalid {name} arguments. Provide a non-empty command '
        f'up to {settings.max_command_chars} characters and optional timeout as a nonnegative '
        'integer in seconds; omit, null, or 0 to use the configured Shell execution limit.')
    return ToolSpec(item, public, execution_mode='sequential', side_effects='may_mutate',
                    permissions=ShellPermission(default='ask', foreground_wait_seconds=task_settings.foreground_wait_seconds))
