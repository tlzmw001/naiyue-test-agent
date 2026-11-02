"""Explicit owner export/download and task-scoped raw-byte upload endpoints."""
import asyncio
from urllib.parse import quote
from fastapi import Request
from starlette.responses import JSONResponse,StreamingResponse

from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.cloud.work import cloud_work
from .auth import owner_authorize,task_bearer
from .http import _body


class ArtifactResponse(StreamingResponse):
    def __init__(self,lease):
        self.lease=lease;record=lease.record
        super().__init__(lease.chunks(),media_type='application/octet-stream',headers={
            'Content-Length':str(record.size),'ETag':'"'+record.sha256+'"','X-Content-Type-Options':'nosniff',
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(record.relative_name.rsplit('/',1)[-1],safe=''),
            'Cache-Control':'private, no-store'})

    async def __call__(self,scope,receive,send):
        try:
            async with asyncio.timeout(self.lease.service.limits.transfer_timeout_seconds):
                await super().__call__(scope,receive,send)
        finally:await await_settled(self.lease.aclose())


def register_artifact_routes(owner,task,runtime):
    service=runtime.artifacts
    owner_path='/api/cloud/v1/tasks/{task_id}/artifacts'
    task_path='/internal/cloud/v1/tasks/{task_id}/artifacts/{artifact_id}'

    @owner.post(owner_path)
    async def capture(request:Request,task_id:str):
        owner_authorize(request,runtime)
        receipt=await service.capture(task_id,await _body(request,runtime))
        return JSONResponse(receipt.model_dump(),status_code=receipt.http_status)

    @owner.get(owner_path+'/{artifact_id}')
    async def metadata(request:Request,task_id:str,artifact_id:str):
        owner_authorize(request,runtime)
        return await cloud_work(runtime,lambda:service.get(task_id,artifact_id))

    @owner.get(owner_path+'/{artifact_id}/bytes')
    async def download(request:Request,task_id:str,artifact_id:str):
        owner_authorize(request,runtime)
        if 'range' in request.headers:raise CloudError('range_not_supported',416)
        return ArtifactResponse(await service.open_download(task_id,artifact_id))

    async def authorize(request,task_id):
        raw=task_bearer(request,runtime)
        await cloud_work(runtime,lambda:runtime.credentials.authorize(raw,task_id,'artifacts:write'))
        return raw

    @task.post(task_path+'/prepare')
    async def prepare(request:Request,task_id:str,artifact_id:str):
        raw=await authorize(request,task_id);value=await _body(request,runtime)
        transfer=await cloud_work(runtime,lambda:service.prepare(raw,task_id,artifact_id,value))
        return transfer.model_dump(exclude={'blob_identity'})

    @task.put(task_path+'/bytes')
    async def upload(request:Request,task_id:str,artifact_id:str):
        raw=await authorize(request,task_id)
        transfer=await service.upload(raw,task_id,artifact_id,request)
        return transfer.model_dump(exclude={'blob_identity'})

    @task.post(task_path+'/commit')
    async def commit(request:Request,task_id:str,artifact_id:str):
        raw=await authorize(request,task_id)
        if await _body(request,runtime)!={}:raise CloudError('invalid_request',422)
        return await service.commit_owned(raw,task_id,artifact_id)
