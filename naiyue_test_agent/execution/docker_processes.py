"""Run-owned Docker process authority; envd owns the actual native processes."""
import asyncio
import base64
import json
from dataclasses import dataclass
from types import SimpleNamespace
from langchain_core.tools import ToolException
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.sandbox import SandboxError,operation_id
from naiyue_test_agent.tools.permissions import require_process_system,PermissionMechanismError
from naiyue_test_agent.tools.processes import ProcessOutcome
from naiyue_test_agent.tools.process_execution import format_process_outcome
from naiyue_test_agent.tools.output import ArchiveResult
from naiyue_test_agent.tools.output_text import OutputAccumulator
from naiyue_test_agent.tools.settings import ProcessToolSettings
from naiyue_test_agent.tools.control import check_tool_stop


@dataclass(frozen=True)
class DockerProcesses:
    coordinator: object
    record: object
    owner: object = None

    @property
    def environment(self):return self.record.environment
    @property
    def owner_session_id(self):return self.record.session_id

    def validate(self):
        row=self.coordinator.environments.check(self.environment.binding_id)
        if row.environment!=self.environment or row.session_id!=self.owner_session_id:
            raise ToolException('env_binding_mismatch')

    def check_access(self,access):
        if require_process_system(access.context) is not self:
            raise PermissionMechanismError('Process authority differs from its binding')

    async def shell(self,name,command,timeout,background,access,*,settings,process_settings,task_settings,progress_settings):
        self.check_access(access)
        root=access.context.scope.identity.agent_path=='root'
        if background and not root:raise ToolException('Background tasks are available only to the root agent.')
        limits=[value for value in (timeout,task_settings.max_execution_seconds) if value]
        payload=dict(name=name,command=command,timeout=min(limits) if limits else None,
            settings=settings.model_dump(mode='json'),process_settings=process_settings.model_dump(mode='json') if process_settings else None,
            output_limit=task_settings.max_background_archive_bytes if background else None)
        return await self._execute('shell',payload,access,settings,background,
            task_settings.foreground_wait_seconds if root else None,task_settings.max_background_archive_bytes)

    async def argv(self,args,timeout,access,*,settings,process_settings,progress_settings):
        self.check_access(access)
        payload=dict(args=args,timeout=timeout or None,settings=settings.model_dump(mode='json'),
            process_settings=process_settings.model_dump(mode='json') if process_settings else None,output_limit=None)
        return await self._execute('argv',payload,access,settings,False,None,settings.max_archive_bytes)

    async def _execute(self,kind,payload,access,settings,background,promote_after,background_limit):
        if self.owner is not None:self.owner.check_admission_open()
        check_tool_stop(access.context.scope.control,'Sandbox process invocation was stopped before submission.')
        ident=operation_id(access);binding=self.environment.binding_id
        metadata=dict(offset=0,saved_bytes=0,max_archive_bytes=background_limit if background else settings.max_archive_bytes,
            tail_limit=settings.max_output_bytes*4,settings=settings.model_dump(mode='json'),background=background,
            agent_path=access.context.scope.identity.agent_path,command_preview=payload.get('command',json.dumps(payload.get('args',[])))[:200])
        handed=False;row=None
        try:
            row=await self.coordinator.asubmit(binding,ident,kind,payload,metadata)
            self.coordinator.raise_process_failure(row)
            check_tool_stop(access.context.scope.control,f'Sandbox process invocation was stopped before handoff; operation_id={ident}. Inspect side effects.')
            if background:
                self.coordinator.watch(binding,ident,self.record.settings['poll_milliseconds']);handed=True
                return json.dumps(self.receipt(row),ensure_ascii=False)
            began=asyncio.get_running_loop().time()
            while not self.coordinator.finished(row):
                if row['state']=='unknown' or row['stale']:
                    raise ToolException(f'env_operation_unknown; operation_id={ident}')
                if self.coordinator._closing or access.context.scope.control is not None and access.context.scope.control.requested:
                    row=await self.coordinator.astop(binding,ident)
                    break
                elapsed=asyncio.get_running_loop().time()-began
                if (promote_after is not None and elapsed>=promote_after and
                        (payload['timeout'] is None or payload['timeout']>max(promote_after,elapsed))):
                    row=await self.coordinator.background(binding,ident,background_limit)
                    self.coordinator.watch(binding,ident,self.record.settings['poll_milliseconds']);handed=True
                    return json.dumps(self.receipt(row),ensure_ascii=False)
                if row['metadata'].get('offset',0)>=row['observed_bytes']:
                    await asyncio.sleep(self.record.settings['poll_milliseconds']/1000)
                row=await self.coordinator.arefresh(binding,ident)
                self.coordinator.raise_process_failure(row)
            return await run_blocking(self.format,row)
        except BaseException as error:
            if not handed and (row is None or row['state'] not in ('completed','failed','stopped')):
                try:await await_settled(self.coordinator.astop(binding,ident))
                except Exception as stop_error:
                    import logging
                    logging.getLogger(__name__).error('sandbox_foreground_stop_unconfirmed error_type=%s',type(stop_error).__name__)
            if isinstance(error,SandboxError):raise ToolException(f'{error.code}; operation_id={ident}') from error
            raise error

    @staticmethod
    def receipt(row):
        return dict(task_id=row['operation_id'],status=row['state'],environment_binding=row['binding_id'],
            stale=row['stale'],query='Use task_list/task_output/task_stop. Running confirms process creation, not service readiness.')

    def format(self,row):
        value=self.coordinator.value(row)
        meta=row['metadata'];settings=ProcessToolSettings.model_validate({key:value for key,value in meta['settings'].items() if key in ProcessToolSettings.model_fields})
        output=OutputAccumulator(settings)
        if meta.get('gap') or meta.get('truncated'):
            output.feed(base64.b64decode(meta.get('tail_base64','')))
        elif meta.get('archive_identity'):
            output.feed(self.coordinator._archive(row).read(meta))
        output.finish()
        fields={key:value[key] for key in ProcessOutcome.__dataclass_fields__}
        if meta.get('gap') or meta.get('truncated'):fields['output_complete']=False
        archive=ArchiveResult({'raw':meta['archive_path']} if meta.get('archive_path') else {},
            not(meta.get('gap') or meta.get('truncated')), 'capture_gap' if meta.get('gap') else 'command_limit' if meta.get('truncated') else None,meta['saved_bytes'])
        text=format_process_outcome(ProcessOutcome(**fields),output,archive,label='Sandbox',stop_reason=value.get('stop_reason'),archive_read_hint=False)
        return text+f'\noperation_id={row["operation_id"]}; remote_observed_bytes={row["observed_bytes"]}; output_is_tail={bool(meta.get("gap") or meta.get("truncated"))}'
