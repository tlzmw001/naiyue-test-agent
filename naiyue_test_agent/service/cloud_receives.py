"""Authenticated local receipts and immutable material reads; no implicit network on GET."""
from fastapi import Request,Query
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.cloud_tasks import UUIDText,CloudRecord
from naiyue_test_agent.contracts.cloud_receive import ReceiveRequest,ReceiveRetry
from naiyue_test_agent.contracts.cloud_local_tasks import SessionId


def register(router,access,domain,body,command_response):
    def public(record):return record.model_copy(update={'result':None,'input_package':None}).model_dump(mode='json')
    @router.post('/cloud-receive-cache/cleanup')
    async def cleanup(request:Request,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);await body(request,CloudRecord)
        _,local=await domain(request,local_project_id,local_session_id)
        result=await manager.receives.cleanup(local,local_session_id)
        return command_response(request,result)

    @router.post('/cloud-tasks/{binding_id}/results/{result_id}/receive')
    async def receive(request:Request,binding_id:UUIDText,result_id:UUIDText,local_project_id:UUIDText):
        manager=access(request);value=await body(request,ReceiveRequest)
        _,local=await domain(request,local_project_id,value.local_session_id)
        result=await manager.receives.submit(local,binding_id,result_id,value.model_dump(mode='json'))
        return command_response(request,public(result),status_code=202)

    @router.get('/cloud-receives/{receive_id}')
    async def status(request:Request,receive_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        result=await manager.receives.read(local,receive_id,local_session_id)
        return command_response(request,public(result))

    @router.post('/cloud-receives/{receive_id}/retry')
    async def retry(request:Request,receive_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);value=await body(request,ReceiveRetry)
        _,local=await domain(request,local_project_id,local_session_id)
        result=await manager.receives.retry(local,receive_id,local_session_id,refresh=value.refresh)
        return command_response(request,public(result),status_code=202)

    @router.post('/cloud-receives/{receive_id}/analysis-draft')
    async def analysis(request:Request,receive_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);await body(request,CloudRecord)
        _,local=await domain(request,local_project_id,local_session_id)
        result=await manager.receives.analysis(local,receive_id,local_session_id)
        return command_response(request,result.model_dump(mode='json'))

    @router.get('/cloud-materials/{material_id}')
    async def metadata(request:Request,material_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        with manager.operation(),local.owner.operation():
            ref=(await run_blocking(local.materials.validate_refs,local_session_id,[material_id]))[0]
        return command_response(request,ref.model_dump(mode='json'))

    @router.get('/cloud-materials/{material_id}/read')
    async def read(request:Request,material_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId,
            offset:int=Query(default=1,ge=1),limit:int=Query(default=100,ge=1,le=10000)):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        local.materials.limits=manager.settings.receive_limits
        result=await local.materials.query(local_session_id,local_project_id,dict(material_id=material_id,offset=offset,limit=limit))
        return command_response(request,result)
