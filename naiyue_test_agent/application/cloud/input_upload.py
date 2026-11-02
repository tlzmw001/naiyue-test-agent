"""Authenticated segments: physical fsync precedes durable offset acknowledgement."""
import asyncio
import time
from pydantic import TypeAdapter

from naiyue_test_agent.contracts.cloud_tasks import CloudError,ContentId
from naiyue_test_agent.contracts.cloud_inputs import InputSegment
from .byte_stream import receive_bytes,upload_headers
from .input_segments import recover
from .work import cloud_work


async def receive(runtime,ident,request):
    service=runtime.inputs
    if service.closing:raise CloudError('cloud_closing',503)
    job=asyncio.current_task();service.uploads.add(job)
    try:
        async with asyncio.timeout(service.limits.upload_timeout_seconds):
            return await _receive(runtime,ident,request)
    except TimeoutError:raise CloudError('transfer_timeout',408) from None
    finally:service.uploads.remove(job)


async def _receive(runtime,ident,request):
    service=runtime.inputs
    with service.exclusive(ident):
        transfer=await cloud_work(runtime,lambda:recover(service,ident),drain_cancel=True)
        try:
            size=int(request.headers.get('content-length',''));offset=int(request.headers.get('upload-offset',''))
            sha=TypeAdapter(ContentId).validate_python(request.headers.get('x-content-sha256'))
        except (ValueError,TypeError):raise CloudError('transfer_headers_mismatch') from None
        upload_headers(request,size,sha,offset=offset)
        if (size<1 or size>transfer.chunk_bytes or offset<0 or offset+size>transfer.size
                or transfer.state not in ('pending','uploading','uploaded')):raise CloudError('transfer_not_writable')
        segment=InputSegment(offset=offset,size=size,sha256=sha)
        replay=offset<transfer.offset
        if replay:
            if segment not in transfer.segments:raise CloudError('transfer_segment_conflict')
        elif offset!=transfer.offset:raise CloudError('transfer_offset_conflict')
        elif len(transfer.segments)>=service.limits.max_segments:raise CloudError('input_budget',413)
        if not replay:
            await cloud_work(runtime,lambda:runtime.resources.ensure(additional_bytes=size))
            transfer=await cloud_work(runtime,lambda:service.repository.save(transfer,transfer.model_copy(
                update={'state':'uploading','updated_at':time.time()})),drain_cancel=True)
        path=await cloud_work(runtime,lambda:service.staging(ident)/'archive',drain_cancel=True)
        fact=await receive_bytes(runtime,request,path=path,size=size,sha256=sha,offset=offset,
            io_bytes=service.limits.io_bytes,replay=replay)
        if replay:return transfer
        return await cloud_work(runtime,lambda:service.repository.save(transfer,transfer.model_copy(update={
            'offset':offset+size,'segments':[*transfer.segments,segment],'archive_identity':fact,
            'state':'uploaded' if offset+size==transfer.size else 'uploading','updated_at':time.time()})),drain_cancel=True)
