"""Task downloads require both a task credential and a persisted run attachment."""
from fastapi import Request
from starlette.responses import JSONResponse
from naiyue_test_agent.application.cloud.attachments import attach,for_command
from naiyue_test_agent.application.cloud.work import cloud_work
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .auth import owner_authorize,task_bearer
from .http import _body
from .artifact_routes import ArtifactResponse


def register(owner,task,runtime):
    @owner.post('/api/cloud/v1/tasks/{task_id}/attachments')
    async def add(request:Request,task_id:str):
        owner_authorize(request,runtime)
        value=await _body(request,runtime)
        result=await cloud_work(runtime,lambda:attach(runtime,task_id,value))
        return JSONResponse(result.model_dump(),status_code=201)

    async def authorized(request,task_id,command_id,attachment_id):
        raw=task_bearer(request,runtime)
        def resolve():
            with runtime.gates.hold(task_id):
                runtime.credentials.authorize(raw,task_id,'inputs:read')
                return for_command(runtime,task_id,command_id,attachment_id)
        return await cloud_work(runtime,resolve)

    @task.get('/internal/cloud/v1/tasks/{task_id}/commands/{command_id}/attachments/{attachment_id}')
    async def metadata(request:Request,task_id:str,command_id:str,attachment_id:str):
        value=await authorized(request,task_id,command_id,attachment_id)
        package=await cloud_work(runtime,lambda:runtime.inputs.get_input(value.input_id))
        return package.model_dump(include={'input_id','archive_sha256','size','codec'})

    @task.get('/internal/cloud/v1/tasks/{task_id}/commands/{command_id}/attachments/{attachment_id}/archive')
    async def archive(request:Request,task_id:str,command_id:str,attachment_id:str):
        value=await authorized(request,task_id,command_id,attachment_id)
        if any(len(request.headers.getlist(h))>1 for h in ('range','if-range')):raise CloudError('invalid_headers',400)
        lease=await runtime.inputs.open_download(value.input_id,task_id=task_id)
        try:
            lease.range(request.headers.get('range'),request.headers.get('if-range'))
            return ArtifactResponse(lease,timeout_seconds=runtime.inputs.limits.upload_timeout_seconds)
        except BaseException:
            from naiyue_test_agent.agent_runtime.async_utils import await_settled
            await await_settled(lease.aclose());raise
