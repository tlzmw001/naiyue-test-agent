"""A synchronous file port sends finite file RPCs to one recorded environment."""
import base64
from langchain_core.tools import ToolException
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.environment import FileResource,ResourceBoundaryError
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.tools.control import check_tool_stop


class DockerFileSystem:
    def __init__(self,controller,record):
        self._controller=controller
        self.environment=record.environment
        self.owner_session_id=record.session_id

    def _request(self,operation,params,*,mutates=False,control=None):
        check_tool_stop(control,'Sandbox file operation stopped before submission.')
        try:
            options={'mutates':mutates}
            if control is not None:options['control']=control
            return self._controller.request(self.environment.binding_id,operation,params,**options)
        except (SandboxError,GitInputError) as error:
            if error.code=='env_resource_outside':raise ResourceBoundaryError('outside_workspace') from error
            if error.code=='env_resource_unavailable':raise OSError('Sandbox resource is unavailable') from error
            raise ToolException(str(error)) from error

    def validate(self):
        try:self._controller.validate(self.environment.binding_id)
        except (SandboxError,GitInputError) as error:raise ToolException(str(error)) from error

    def prepare_target(self,path,operation):
        return self._request('prepare_target',dict(path=path,operation=operation))

    def inspect_candidate(self,path,operation):return self.prepare_target(path,operation)

    def verify_target(self,subject):return self._request('verify_target',dict(subject=subject))

    def read(self,subject,offset,limit,*,settings,control,progress):
        return self._request('read',dict(subject=subject,offset=offset,limit=limit,settings=settings.model_dump(mode='json')),control=control)

    def write(self,subject,content,*,settings,control,progress,read_allowed):
        result=self._request('write',dict(subject=subject,content=content,read_allowed=read_allowed,
            settings=settings.model_dump(mode='json')),mutates=True,control=control)
        if not isinstance(result,list) or len(result)!=2 or not isinstance(result[0],str) or not isinstance(result[1],dict):
            raise ToolException('env_operation_unknown')
        return result[0],result[1]

    def edit(self,subject,edits,*,settings,control,progress):
        result=self._request('edit',dict(subject=subject,edits=[item.model_dump(mode='json') for item in edits],
            settings=settings.model_dump(mode='json')),mutates=True,control=control)
        if not isinstance(result,list) or len(result)!=2 or not isinstance(result[0],str) or not isinstance(result[1],dict):
            raise ToolException('env_operation_unknown')
        return result[0],result[1]

    @staticmethod
    def _access(access):
        auth=access.context.authorization
        return dict(permit=auth.permit,permission_rules=auth.permission_rules.model_dump(mode='json'),
            session_allow=auth.session_allow,permission_mode=auth.permission_mode)

    def list(self,subject,limit,access,*,settings,progress):
        return self._request('list',dict(subject=subject,limit=limit,access=self._access(access),
            settings=settings.model_dump(mode='json')),control=access.context.scope.control)

    async def find(self,subject,pattern,limit,access,*,settings,process_settings,progress,timeout):
        return await run_blocking(self._request,'find',dict(subject=subject,pattern=pattern,limit=limit,
            access=self._access(access),settings=settings.model_dump(mode='json'),
            process_settings=process_settings.model_dump(mode='json') if process_settings else None,timeout=timeout),
            control=access.context.scope.control)

    async def grep(self,subject,pattern,glob,ignore_case,literal,context_lines,limit,access,
                   *,settings,process_settings,progress,timeout):
        return await run_blocking(self._request,'grep',dict(subject=subject,pattern=pattern,glob=glob,
            ignore_case=ignore_case,literal=literal,context_lines=context_lines,limit=limit,access=self._access(access),
            settings=settings.model_dump(mode='json'),process_settings=process_settings.model_dump(mode='json') if process_settings else None,
            timeout=timeout),control=access.context.scope.control)

    def resource_info(self,path):return FileResource(**self._request('resource_info',dict(path=path)))
    def resolve_resource(self,path):return self._request('resolve_resource',dict(path=path))
    def list_resources(self,path):return tuple(self._request('list_resources',dict(path=path)))
    def read_resource(self,path,max_bytes):
        value=self._request('read_resource',dict(path=path,max_bytes=max_bytes))
        return base64.b64decode(value['bytes_base64'],validate=True)
