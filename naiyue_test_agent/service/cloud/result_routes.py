"""Result metadata stays authenticated; raw files reuse the existing artifact transport."""
from fastapi import Request,Query
from starlette.responses import JSONResponse
from naiyue_test_agent.application.cloud.work import cloud_work
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .auth import owner_authorize,task_bearer
from .http import _body


def register_result_routes(owner,task,runtime):
    service=runtime.results;op='/api/cloud/v1/tasks/{task_id}/results';tp='/internal/cloud/v1/tasks/{task_id}/results/{result_id}'
    @owner.get(op)
    async def listing(request:Request,task_id:str,offset:int=Query(default=0,ge=0,le=1000000),limit:int=Query(default=20,ge=1,le=100)):
        owner_authorize(request,runtime,query_fields=('offset','limit'))
        return await cloud_work(runtime,lambda:service.list(task_id,offset=offset,limit=limit))
    @owner.get(op+'/{result_id}')
    async def read(request:Request,task_id:str,result_id:str):
        owner_authorize(request,runtime)
        return await cloud_work(runtime,lambda:service.get(task_id,result_id))
    @owner.get(op+'/{result_id}/changes')
    async def changes(request:Request,task_id:str,result_id:str,offset:int=Query(default=0,ge=0,le=1000000),limit:int=Query(default=50,ge=1,le=100)):
        from naiyue_test_agent.application.cloud.result_views import changes
        owner_authorize(request,runtime,query_fields=('offset','limit'))
        return await cloud_work(runtime,lambda:changes(service,task_id,result_id,offset,limit))
    @owner.get(op+'/{result_id}/files/{file_id}/preview')
    async def preview(request:Request,task_id:str,result_id:str,file_id:str,offset:int=Query(default=0,ge=0,le=1099511627776),limit:int=Query(default=16384,ge=4,le=65536)):
        from naiyue_test_agent.application.cloud.result_views import preview
        owner_authorize(request,runtime,query_fields=('offset','limit'))
        return await cloud_work(runtime,lambda:preview(service,task_id,result_id,file_id,offset,limit))
    @owner.get(op+'/{result_id}/files/{file_id}/bytes')
    async def download(request:Request,task_id:str,result_id:str,file_id:str):
        from naiyue_test_agent.application.cloud.result_views import file_slot
        from .artifact_routes import download_response
        owner_authorize(request,runtime)
        await cloud_work(runtime,lambda:file_slot(service,task_id,result_id,file_id))
        return await download_response(request,runtime.artifacts,task_id,file_id)
    @owner.post(op+'/{result_id}/{action}')
    async def action(request:Request,task_id:str,result_id:str,action:str):
        owner_authorize(request,runtime)
        if action not in ('retry','abandon'):raise CloudError('invalid_request',422)
        value=await _body(request,runtime)
        receipt=await cloud_work(runtime,lambda:service.action(task_id,result_id,action,value),drain_cancel=True)
        return JSONResponse(receipt.model_dump(mode='json'),status_code=receipt.http_status)
    async def authorize(request,task_id):
        raw=task_bearer(request,runtime)
        await cloud_work(runtime,lambda:runtime.credentials.authorize(raw,task_id,'artifacts:write'))
        return raw
    @task.get(tp)
    async def task_read(request:Request,task_id:str,result_id:str):
        await authorize(request,task_id)
        return await cloud_work(runtime,lambda:service.get(task_id,result_id))
    @task.post(tp+'/{action}')
    async def task_action(request:Request,task_id:str,result_id:str,action:str):
        raw=await authorize(request,task_id);value=await _body(request,runtime)
        if action=='prepare':fn=lambda:service.prepare(raw,task_id,result_id,value)
        elif action=='fail':fn=lambda:service.failed(raw,task_id,result_id,value)
        elif action in ('begin','commit'):
            if value!={}:raise CloudError('invalid_request',422)
            fn=lambda:service.begin(raw,task_id,result_id) if action=='begin' else service.commit(raw,task_id,result_id)
        else:raise CloudError('invalid_request',422)
        return await cloud_work(runtime,fn,drain_cancel=True)
