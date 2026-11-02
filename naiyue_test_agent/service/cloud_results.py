"""Local result routes resolve a fixed owner binding; IDs never select a remote URL."""
from fastapi import Request,Query
from naiyue_test_agent.contracts.cloud_tasks import UUIDText,CloudError
from naiyue_test_agent.contracts.cloud_local_tasks import SessionId,ManualResultAction


def register(router,access,domain,bounded_view,body,command_response):
    base='/cloud-tasks/{binding_id}/results'
    @router.get(base)
    async def listing(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId,
            offset:int=Query(default=0,ge=0,le=1000000),limit:int=Query(default=20,ge=1,le=100)):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        return await bounded_view(request,manager,local,binding_id,local_session_id,'results',dict(offset=offset,limit=limit))
    @router.get(base+'/{result_id}')
    async def read(request:Request,binding_id:UUIDText,result_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        return await bounded_view(request,manager,local,binding_id,local_session_id,'result',dict(result_id=result_id))
    @router.get(base+'/{result_id}/changes')
    async def changes(request:Request,binding_id:UUIDText,result_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId,
            offset:int=Query(default=0,ge=0,le=1000000),limit:int=Query(default=50,ge=1,le=100)):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        return await bounded_view(request,manager,local,binding_id,local_session_id,'result_changes',dict(result_id=result_id,offset=offset,limit=limit))
    @router.get(base+'/{result_id}/files/{file_id}/preview')
    async def preview(request:Request,binding_id:UUIDText,result_id:UUIDText,file_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId,
            offset:int=Query(default=0,ge=0,le=1099511627776),limit:int=Query(default=16384,ge=4,le=65536)):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        return await bounded_view(request,manager,local,binding_id,local_session_id,'result_preview',dict(result_id=result_id,file_id=file_id,offset=offset,limit=limit))
    @router.post(base+'/{result_id}/actions')
    async def action(request:Request,binding_id:UUIDText,result_id:UUIDText,local_project_id:UUIDText):
        manager=access(request);value=await body(request,ManualResultAction)
        if value.result_id!=result_id:raise CloudError('result_identity_conflict')
        _,local=await domain(request,local_project_id,value.local_session_id)
        return command_response(request,await manager.commands.submit(local,binding_id,'result_'+value.action,value.model_dump(mode='json')),status_code=202)
    @router.get(base+'/{result_id}/files/{file_id}/bytes')
    async def download(request:Request,binding_id:UUIDText,result_id:UUIDText,file_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        from naiyue_test_agent.application.cloud_local.result_download import response
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        return await response(request,manager,local,binding_id,local_session_id,result_id,file_id)
