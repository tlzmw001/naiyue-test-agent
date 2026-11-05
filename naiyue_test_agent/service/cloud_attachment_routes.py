"""Authenticated local file selection, task-scoped upload custody and progress."""
from fastapi import Request,Query
from pydantic import Field,field_validator
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.cloud_tasks import CloudRecord,CloudError,UUIDText
from naiyue_test_agent.contracts.cloud_local_tasks import SessionId
from naiyue_test_agent.contracts.cloud_inputs import file_path
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.cloud_files.source_scope import tree
from typing import Literal


class UploadFiles(CloudRecord):
    local_session_id:SessionId
    attachment_id:UUIDText
    display_name:str=Field(min_length=1,max_length=255)
    paths:list[str]=Field(min_length=1,max_length=100000)

    @field_validator('paths')
    @classmethod
    def valid(cls,paths):return [file_path(path) for path in paths]


def register(router,access,domain,body):
    async def trusted(request,pid,sid):
        manager=access(request);project,local=await domain(request,pid,sid)
        if not project.trusted:raise CloudError('cloud_project_trust_required',403)
        if not isinstance(project.source,LocalSource):raise CloudError('cloud_local_source_required')
        return manager,project,local

    @router.get('/cloud-files')
    async def browse(request:Request,local_project_id:UUIDText,local_session_id:SessionId,
            path:str=Query(default='',max_length=1024),offset:int=Query(default=0,ge=0,le=500000)):
        manager,project,local=await trusted(request,local_project_id,local_session_id)
        with local.owner.operation():
            if local.authority:
                execution=await run_blocking(local.authority.resolve,local_project_id)
                if execution.path!=project.source.path:raise CloudError('cloud_source_identity_changed')
            return await run_blocking(tree,project.source.path,manager.settings.input_limits,path=path,offset=offset,ordinary=True)

    @router.post('/cloud-tasks/{binding_id}/attachments')
    async def upload(request:Request,binding_id:UUIDText,local_project_id:UUIDText):
        access(request);value=await body(request,UploadFiles)
        manager,project,local=await trusted(request,local_project_id,value.local_session_id)
        return await manager.attachments.submit(local,binding_id,value.local_session_id,project.source.path,
            value.model_dump(exclude={'local_session_id'}))

    @router.get('/cloud-tasks/{binding_id}/attachments')
    async def listing(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        return await manager.attachments.list(local,binding_id,local_session_id)

    @router.post('/cloud-tasks/{binding_id}/attachments/{attachment_id}/{action}')
    async def action(request:Request,binding_id:UUIDText,attachment_id:UUIDText,action:Literal['cancel','retry'],
            local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        if action=='retry':await trusted(request,local_project_id,local_session_id)
        return await manager.attachments.action(local,binding_id,local_session_id,attachment_id,action)


def register_queue(router,access,domain,body):
    from naiyue_test_agent.contracts.cloud_local_tasks import ManualMessage
    @router.get('/cloud-tasks/{binding_id}/queued-message')
    async def read(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        return await manager.queue.read(local,binding_id,local_session_id)

    @router.post('/cloud-tasks/{binding_id}/queued-message')
    async def submit(request:Request,binding_id:UUIDText,local_project_id:UUIDText):
        manager=access(request);value=await body(request,ManualMessage)
        _,local=await domain(request,local_project_id,value.local_session_id)
        return await manager.queue.submit(local,binding_id,value.local_session_id,value.model_dump())

    @router.post('/cloud-tasks/{binding_id}/queued-message/{command_id}/cancel')
    async def cancel(request:Request,binding_id:UUIDText,command_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        return await manager.queue.cancel(local,binding_id,local_session_id,command_id)
