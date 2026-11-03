"""Owner-only input routes; authenticate before consuming metadata or raw bytes."""
from fastapi import Request
from starlette.responses import JSONResponse
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.cloud.input_upload import receive
from .auth import owner_authorize
from .http import _body
from naiyue_test_agent.application.cloud.work import cloud_work


def register_input_routes(owner,runtime):
    prefix='/api/cloud/v1'

    @owner.post(prefix+'/transfers')
    async def create(request:Request):
        owner_authorize(request,runtime)
        value=await _body(request,runtime)
        receipt=await cloud_work(runtime,lambda:runtime.inputs.create(value))
        return JSONResponse(receipt.model_dump(),status_code=receipt.http_status)

    @owner.get(prefix+'/transfers/{ident}')
    async def status(request:Request,ident:str):
        owner_authorize(request,runtime)
        from naiyue_test_agent.application.cloud.input_segments import status
        return await status(runtime.inputs,ident)

    @owner.put(prefix+'/transfers/{ident}/bytes')
    async def upload(request:Request,ident:str):
        owner_authorize(request,runtime)
        return await receive(runtime,ident,request)

    @owner.post(prefix+'/transfers/{ident}/commit')
    async def commit(request:Request,ident:str):
        owner_authorize(request,runtime)
        if await _body(request,runtime)!={}:raise CloudError('invalid_request',422)
        return await runtime.inputs.perform(ident,lambda:runtime.inputs.commit(ident))

    @owner.delete(prefix+'/transfers/{ident}')
    async def cancel(request:Request,ident:str):
        owner_authorize(request,runtime)
        return await runtime.inputs.perform(ident,lambda:runtime.inputs.cancel(ident))

    @owner.get(prefix+'/inputs/{ident}')
    async def input_info(request:Request,ident:str):
        owner_authorize(request,runtime)
        return await cloud_work(runtime,lambda:runtime.inputs.get_input(ident))

    @owner.get(prefix+'/inputs/{ident}/archive')
    async def archive(request:Request,ident:str):
        owner_authorize(request,runtime)
        from naiyue_test_agent.agent_runtime.async_utils import await_settled
        from .artifact_routes import ArtifactResponse
        if any(len(request.headers.getlist(h))>1 for h in ('range','if-range')):raise CloudError('invalid_headers',400)
        lease=await runtime.inputs.open_download(ident)
        try:
            lease.range(request.headers.get('range'),request.headers.get('if-range'))
            return ArtifactResponse(lease,timeout_seconds=runtime.inputs.limits.upload_timeout_seconds)
        except BaseException:
            await await_settled(lease.aclose());raise

    @owner.post(prefix+'/tasks')
    async def create_task(request:Request):
        owner_authorize(request,runtime)
        value=await _body(request,runtime)
        receipt=await cloud_work(runtime,lambda:runtime.inputs.create_task(value))
        return JSONResponse(receipt.model_dump(),status_code=receipt.http_status)
