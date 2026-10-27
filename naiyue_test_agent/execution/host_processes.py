"""Host process capability bound to the same authority as this run's files."""
import asyncio
from contextvars import Context
from dataclasses import dataclass, field
import json
import logging
import shutil

from langchain_core.tools import ToolException

from naiyue_test_agent.agent_runtime.async_utils import run_blocking, await_settled
from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessRequest
from naiyue_test_agent.tools.process_execution import execute_process_request, execute_managed_foreground
from .host_files import HostFileSystem
from .host_shell import BashAdapter, PowerShellAdapter, build_environment

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HostProcesses:
    files: HostFileSystem
    _prepared: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        if not isinstance(self.files, HostFileSystem) or self.files.environment.kind != 'host':
            raise ValueError('Host processes require a verified Host file binding')

    @property
    def environment(self):
        return self.files.environment

    @property
    def owner_session_id(self):
        return self.files.owner_session_id

    def validate(self):
        self.files.validate()

    async def _shell(self, name, settings, process_settings):
        # Only this capability's event loop mutates the map, before suspending.
        key = (name, settings.model_dump_json(), process_settings.model_dump_json() if process_settings else None)
        task = self._prepared.get(key)
        if task is None:
            def prepare():
                adapter = {'bash': BashAdapter, 'powershell': PowerShellAdapter}[name]()
                runner = HostProcessRunner(process_settings)
                executable = adapter.resolve_executable(settings)
                version = adapter.probe_version(executable, settings, runner, self.files.execution_root.path) if executable else 'unavailable'
                if not executable:
                    logger.warning('shell_interpreter_unavailable shell=%s', name)
                    raise ToolException(f'{adapter.label} interpreter is unavailable. Configure its executable or install it in the selected environment.')
                return adapter, runner, executable, version
            task = asyncio.create_task(run_blocking(prepare), context=Context())
            self._prepared[key] = task
        return await await_settled(task)

    async def shell(self, name, command, timeout, background, access, *, settings,
                    process_settings, task_settings, progress_settings):
        await run_blocking(self.validate)
        context = access.context
        if background and context.scope.identity.agent_path != 'root':
            raise ToolException('Background tasks are available only to the root agent.')
        adapter, runner, executable, version = await self._shell(name, settings, process_settings)
        limits = [value for value in (timeout, task_settings.max_execution_seconds) if value]
        effective_timeout = min(limits) if limits else None
        request = await run_blocking(adapter.build_request, executable, version, command, effective_timeout,
                                    self.files.execution_root.path, settings)
        from dataclasses import replace
        request = replace(request, execution_environment=self.environment.public())
        if background or context.scope.identity.agent_path == 'root' and context.scope.capabilities.process_tasks is not None:
            from naiyue_test_agent.tools.background_tasks import task_context
            port, scope = task_context(access)
            scope['environment'] = self.environment
            if background:
                receipt = await port.submit(**scope, run_id=context.scope.identity.run_id,
                    tool_call_id=access.tool_call_id, agent_path=context.scope.identity.agent_path,
                    command=command, name=name, request=request, runner=runner,
                    settings=settings.model_copy(update={'max_archive_bytes': task_settings.max_background_archive_bytes}),
                    control=context.scope.control)
                return json.dumps({**receipt, 'query': 'Use task_list/task_output; task_stop stops this task. '
                    'running confirms process creation, not service readiness.'}, ensure_ascii=False)
            return await execute_managed_foreground(port, scope, request, access, command=command, name=name,
                label=adapter.label, settings=settings, runner=runner, task_settings=task_settings,
                progress_settings=progress_settings)
        # Recheck after preparation, before process capture and spawn.
        await run_blocking(self.validate)
        return await execute_process_request(request, access, name=name, label=adapter.label,
            settings=settings, runner=runner, progress_settings=progress_settings)

    async def argv(self, args, timeout, access, *, settings, process_settings, progress_settings):
        await run_blocking(self.validate)
        def prepare():
            environment = build_environment(settings.environment)
            path = next((value for key, value in environment.items() if key.casefold() == 'path'), '')
            executable = shutil.which(settings.executable, path=path)
            if executable is None:
                raise ToolException('aitest executable is unavailable. Install aitest-kit in the selected environment or configure tools.aitest_kit.executable and prefix_args.')
            return ProcessRequest([executable, *settings.prefix_args, *args], self.files.execution_root.path,
                environment, timeout or None, merge_output=True, eof_policy='shell',
                execution_environment=self.environment.public())
        request = await run_blocking(prepare)
        await run_blocking(self.validate)
        return await execute_process_request(request, access, name='aitest_kit', label='AITest', settings=settings,
            runner=HostProcessRunner(process_settings), progress_settings=progress_settings)
