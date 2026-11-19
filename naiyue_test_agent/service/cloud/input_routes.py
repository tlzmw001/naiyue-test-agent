"""Owner-only input routes; authenticate before consuming metadata or raw bytes."""
from fastapi import Request
from pydantic import TypeAdapter
from naiyue_test_agent.contracts.cloud_inputs import AcquireInputUse,CreateTransfer
from naiyue_test_agent.contracts.cloud_tasks import UUIDText,ContentId
from naiyue_test_agent.contracts.cloud_versions import OWNER_PROTOCOL
from starlette.responses import JSONResponse
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.cloud.input_upload import receive
from .auth import owner_authorize
from .http import _body
from naiyue_test_agent.application.cloud.work import cloud_work


def use_header(request, *, required):
    values=request.headers.getlist('x-input-use-id')
    if len(values)>1:raise CloudError('invalid_headers',400)
    if not values:
        if required:raise CloudError('input_use_required')
        return None
    return TypeAdapter(UUIDText).validate_python(values[0])


def register_input_routes(owner,runtime):
    prefix='/api/cloud/v1'

    @owner.post(prefix+'/input-uses')
    async def acquire(request:Request):
        owner_authorize(request,runtime)
        value=AcquireInputUse.model_validate(await _body(request,runtime))
        return await cloud_work(runtime,lambda:runtime.inputs.uses.acquire(
            value,runtime.inputs.limits,runtime.settings.max_receipts))

    @owner.get(prefix+'/input-uses/{use_id}')
    async def get_use(request:Request,use_id:UUIDText):
        owner_authorize(request,runtime)
        return await cloud_work(runtime,lambda:runtime.inputs.uses.get(use_id))

    @owner.delete(prefix+'/input-uses/{use_id}')
    async def release(request:Request,use_id:UUIDText):
        owner_authorize(request,runtime)
        use=await cloud_work(runtime,lambda:runtime.inputs.uses.get(use_id))
        with runtime.inputs.exclusive('input:'+use['input_id']):
            return await cloud_work(runtime,lambda:runtime.inputs.uses.release(use_id),drain_cancel=True)

    @owner.post(prefix+'/transfers')
    async def create(request:Request):
        owner_authorize(request,runtime)
        value=CreateTransfer.model_validate(await _body(request,runtime))
        use_id=use_header(request,required=False)
        receipt=await runtime.inputs.read_package(value.input_id,lambda:runtime.inputs.create(value,use_id=use_id))
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
    async def input_info(request:Request,ident:ContentId):
        owner_authorize(request,runtime)
        return await runtime.inputs.read_package(ident,lambda:runtime.inputs.get_input(ident))

    @owner.get(prefix+'/inputs/{ident}/archive')
    async def archive(request:Request,ident:str):
        owner_authorize(request,runtime)
        from naiyue_test_agent.agent_runtime.async_utils import await_settled
        from .artifact_routes import ArtifactResponse
        if any(len(request.headers.getlist(h))>1 for h in ('range','if-range')):raise CloudError('invalid_headers',400)
        if request.headers.get('x-cloud-protocol')!=str(OWNER_PROTOCOL):raise CloudError('cloud_protocol_mismatch',426)
        lease=await runtime.inputs.open_download(ident,use_id=use_header(request,required=True))
        try:
            lease.range(request.headers.get('range'),request.headers.get('if-range'))
            return ArtifactResponse(lease,timeout_seconds=runtime.inputs.limits.upload_timeout_seconds)
        except BaseException:
            await await_settled(lease.aclose());raise

    @owner.post(prefix+'/tasks')
    async def create_task(request:Request):
        owner_authorize(request,runtime)
        value=await _body(request,runtime)
        use_id=use_header(request,required=value.get('input_id') is not None)
        def create():
            return runtime.inputs.create_task(value,use_id=use_id)
        ident=value.get('input_id')
        receipt=await (runtime.inputs.read_package(ident,create) if ident is not None else cloud_work(runtime,create))
        return JSONResponse(receipt.model_dump(),status_code=receipt.http_status)
