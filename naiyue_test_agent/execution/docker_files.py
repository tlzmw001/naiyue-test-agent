"""A file capability awaits the persistent channel of one recorded environment."""
import base64
from langchain_core.tools import ToolException
from naiyue_test_agent.contracts.environment import ResourceBoundaryError
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.tools.control import check_tool_stop


class DockerFileSystem:
    def __init__(self,controller,record):
        self._controller=controller
        self.environment=record.environment
        self.owner_session_id=record.session_id
        self.message_bytes=record.settings['max_message_bytes']

    def validate(self):
        try:self._controller.check(self.environment.binding_id)
        except (SandboxError,GitInputError) as error:raise ToolException(str(error)) from error

    async def _arequest(self,operation,params,*,mutates=False,control=None):
        check_tool_stop(control,'Sandbox file operation stopped before submission.')
        try:return await self._controller.arequest(self.environment.binding_id,operation,params,mutates=mutates,control=control)
        except (SandboxError,GitInputError) as error:
            if error.code=='env_resource_outside':raise ResourceBoundaryError('outside_workspace') from error
            if error.code=='env_resource_unavailable':raise OSError('Sandbox resource is unavailable') from error
            raise ToolException(str(error)) from error

    async def prepare_target(self,path,operation):
        return await self._arequest('prepare_target',dict(path=path,operation=operation))

    def verify_target(self,subject):
        # The authoritative path/approval check and IO are atomic inside envd.
        if subject.get('execution_environment')!=self.environment.public():raise ToolException('env_binding_mismatch')
        return subject['resolved']

    async def read(self,subject,offset,limit,*,settings,control,progress):
        return await self._arequest('read',dict(subject=subject,offset=offset,limit=limit,settings=settings.model_dump(mode='json')),control=control)

    async def write(self,subject,content,*,settings,control,progress,read_allowed):
        result=await self._arequest('write',dict(subject=subject,content=content,read_allowed=read_allowed,
            settings=settings.model_dump(mode='json')),mutates=True,control=control)
        if not isinstance(result,list) or len(result)!=2 or not isinstance(result[0],str) or not isinstance(result[1],dict):
            raise ToolException('env_operation_unknown')
        return result[0],result[1]

    async def edit(self,subject,edits,*,settings,control,progress):
        result=await self._arequest('edit',dict(subject=subject,edits=[item.model_dump(mode='json') for item in edits],
            settings=settings.model_dump(mode='json')),mutates=True,control=control)
        if not isinstance(result,list) or len(result)!=2 or not isinstance(result[0],str) or not isinstance(result[1],dict):
            raise ToolException('env_operation_unknown')
        return result[0],result[1]

    @staticmethod
    def _access(access):
        auth=access.context.authorization
        return dict(permit=auth.permit,permission_rules=auth.permission_rules.model_dump(mode='json'),
            session_allow=auth.session_allow,permission_mode=auth.permission_mode)

    async def list(self,subject,limit,access,*,settings,progress):
        return await self._arequest('list',dict(subject=subject,limit=limit,access=self._access(access),
            settings=settings.model_dump(mode='json')),control=access.context.scope.control)

    async def find(self,subject,pattern,limit,access,*,settings,process_settings,progress,timeout):
        return await self._arequest('find',dict(subject=subject,pattern=pattern,limit=limit,
            access=self._access(access),settings=settings.model_dump(mode='json'),
            process_settings=process_settings.model_dump(mode='json') if process_settings else None,timeout=timeout),
            control=access.context.scope.control)

    async def grep(self,subject,pattern,glob,ignore_case,literal,context_lines,limit,access,
                   *,settings,process_settings,progress,timeout):
        return await self._arequest('grep',dict(subject=subject,pattern=pattern,glob=glob,
            ignore_case=ignore_case,literal=literal,context_lines=context_lines,limit=limit,access=self._access(access),
            settings=settings.model_dump(mode='json'),process_settings=process_settings.model_dump(mode='json') if process_settings else None,
            timeout=timeout),control=access.context.scope.control)

    async def read_resource(self,path,max_bytes):
        value=await self._arequest('read_resource',dict(path=path,max_bytes=max_bytes))
        return base64.b64decode(value['bytes_base64'],validate=True)

    async def resource_view(self,*,instructions,skills):
        from .resource_view import ResourceView
        root=self.environment.path(self.environment.root)
        paths=[str(root/name) for name in ('AGENTS.md','CLAUDE.md')] if instructions else []
        directories=[{'path':str(root/f'.{source}'/'skills'),'leaf':'SKILL.md'}
            for source in ('agents','claude')] if skills else []
        index=await self._arequest('resource_index',{'paths':paths,'directories':directories})
        return ResourceView(self.environment,index)

    async def load_resources(self,view,paths):
        # Reserve envelope/path space and base64 expansion at the actual frozen budget.
        batch=max(1,min(32,(self.message_bytes-8192)//(88000+4096)))
        for offset in range(0,len(paths),batch):
            result=await self._arequest('resource_read_batch',{'paths':paths[offset:offset+batch],'max_bytes':65536})
            view.bodies.update(result['entries'])
