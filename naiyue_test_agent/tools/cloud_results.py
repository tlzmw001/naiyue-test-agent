"""Main-session access to fixed cloud artifacts through the local file authority."""
import json
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from naiyue_test_agent.contracts.cloud_tasks import CloudError, UUIDText
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.contracts.run_context import RuntimeContext
from .permissions import (FilePermission, PermissionMechanismError, ToolPermission,
                          permission_binding, require_file_system, require_tool_permit)
from .registry import ToolSpec
from .schemas import build_tool_schemas


class CloudResultInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    action: Literal['query','download','status']
    binding_id: UUIDText | None = Field(default=None, exclude_if=lambda v:v is None)
    result_id: UUIDText | None = Field(default=None, exclude_if=lambda v:v is None)
    artifact_id: UUIDText | None = Field(default=None, exclude_if=lambda v:v is None)
    operation_id: UUIDText | None = Field(default=None, exclude_if=lambda v:v is None)
    path: str | None = Field(default=None, max_length=4096, exclude_if=lambda v:v is None)
    force: bool | None = Field(default=None, exclude_if=lambda v:v is None)
    offset: int | None = Field(default=None, ge=0, le=1000000, exclude_if=lambda v:v is None)
    limit: int | None = Field(default=None, ge=1, le=100, exclude_if=lambda v:v is None)

    @model_validator(mode='after')
    def action_fields(self):
        supplied=self.model_fields_set-{'action','runtime'}
        if self.action=='query':
            if supplied-{'binding_id','result_id','offset','limit'} or self.result_id and not self.binding_id:
                raise ValueError('Invalid cloud result query fields')
            self.offset=0 if self.offset is None else self.offset
            self.limit=20 if self.limit is None else self.limit
        elif self.action=='status' or self.operation_id is not None:
            if supplied!={'operation_id'} or self.operation_id is None:
                raise ValueError('Only operation_id is accepted for status or resume')
        else:
            required={'binding_id','result_id','artifact_id','path'}
            if supplied-required-{'force'} or any(getattr(self,k) is None for k in required):
                raise ValueError('Download requires binding_id, result_id, artifact_id and path')
            destination=Path(self.path)
            if not destination.is_absolute() or '..' in destination.parts or not destination.name:
                raise ValueError('Use an absolute destination file path')
            self.force=False if self.force is None else self.force
        return self


def result_port(context):
    scope=context.scope
    port=scope.capabilities.cloud_results
    if (port is None or scope.identity.agent_path!='root' or scope.environment is None
            or scope.environment.kind!='host' or not scope.identity.project_id):
        raise ToolException('Cloud results require the source main session in a local project.')
    require_file_system(context)
    return port


class CloudResultPermission(FilePermission):
    def prepare(self,args,context):
        port=result_port(context)
        if args['action']!='download':
            return ToolPermission(default='allow').prepare(args,context) | {'operation':'read'}
        try:
            plan=port.plan(context.scope.identity.session_id,args['operation_id']) if 'operation_id' in args else args
        except CloudError as error:
            raise ToolException(error.code) from None
        target=super().prepare({'path':plan['path']},context)
        if target['requested']!=target['resolved']:
            raise ToolException('Cloud result destination must not traverse a link.')
        return target | {'download_plan':{k:plan[k] for k in ('binding_id','result_id','artifact_id','path','force')}}

    def default_decision(self,subject):
        return super().default_decision(subject) if subject['operation']=='write' else 'allow'


def create_cloud_result_tool():
    public,execution=build_tool_schemas('CloudResultInput',CloudResultInput)
    async def execute(runtime: ToolRuntime[RuntimeContext], **arguments):
        request=public.model_validate(arguments).model_dump(mode='json')
        permit=require_tool_permit(runtime)
        identity=runtime.context.scope.identity
        batch=runtime.state.get('tool_execution',{})
        call=next((item for item in batch.get('calls',[]) if item['id']==runtime.tool_call_id),None)
        if (call is None or permit!=permission_binding(identity,batch.get('batch_id'),call)
                or permit.get('tool_name')!='cloud_result' or permit['subject']['args']!=request):
            raise PermissionMechanismError('Cloud result call does not match its execution permit')
        port=result_port(runtime.context)
        sid=identity.session_id
        action=request['action']
        try:
            if action=='query':
                result=await port.query(sid,request)
            elif action=='status':
                result=await port.status(sid,request['operation_id'])
            else:
                subject=permit['subject']
                files=require_file_system(runtime.context)
                cid=request.get('operation_id') or str(uuid5(NAMESPACE_URL,json.dumps(
                    ['cloud_result',sid,identity.run_id,batch.get('batch_id'),runtime.tool_call_id])))
                result=await port.download(sid,cid,subject['download_plan'],
                    resume='operation_id' in request,verify=lambda:files.verify_target(subject))
        except (CloudError,ProjectError) as error:
            missing=error.code=='project_directory_missing'
            reason='The destination folder does not exist.' if missing else error.code.replace('_',' ')
            result=dict(action=action,state='failed',error=dict(code=error.code,
                step='preflight' if action=='download' else action,reason=reason,
                retryable=missing or error.code in ('cloud_connection_failed','cloud_delivery_capacity','cloud_receive_busy')))
        return json.dumps(result,ensure_ascii=False)
    tool=StructuredTool.from_function(coroutine=execute,name='cloud_result',args_schema=execution,
        description='Query cloud tasks associated with this main session, their results, and ordinary artifacts. '
        'Download one fixed artifact to an absolute file path, then check status(operation_id) before using the file. '
        'On conflict ask the user to overwrite or choose a different filename. Set force=true only when the user '
        'has asked to overwrite. Use download(operation_id) to resume the same operation.')
    return ToolSpec(tool,public,execution_mode='sequential',side_effects='may_mutate',
                    permissions=CloudResultPermission(operation='write'))
