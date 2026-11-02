"""Shared complete-body IO only; callers own purpose, auth, status and commit."""
import hashlib
import logging
import os

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_file,open_store_readonly,sync_store_directory
from .work import cloud_work

logger=logging.getLogger(__name__)


def upload_headers(request,size,sha256,*,offset=0):
    for key in ('content-length','content-type','upload-offset','x-content-sha256','content-encoding'):
        if len(request.headers.getlist(key))>1:raise CloudError('invalid_headers',400)
    if request.headers.get('content-type')!='application/octet-stream' or request.headers.get('content-encoding') is not None:
        raise CloudError('invalid_upload_type',415)
    if (request.headers.get('content-length')!=str(size) or request.headers.get('upload-offset')!=str(offset)
            or request.headers.get('x-content-sha256')!=sha256):raise CloudError('transfer_headers_mismatch')


async def receive_bytes(runtime,request,*,path,size,sha256,io_bytes,replay=False,offset=0):
    stream=None;sha=hashlib.sha256();received=0
    try:
        if not replay:
            def opened():
                value=os.fdopen(open_store_file(path),'r+b')
                try:value.seek(offset);value.truncate(offset)
                except BaseException:value.close();raise
                return value
            stream=await cloud_work(runtime,opened,drain_cancel=True)
        async for chunk in request.stream():
            received+=len(chunk)
            if received>size:raise CloudError('transfer_length_mismatch')
            for piece_offset in range(0,len(chunk),io_bytes):
                piece=chunk[piece_offset:piece_offset+io_bytes];sha.update(piece)
                if stream is not None:await cloud_work(runtime,lambda:stream.write(piece),drain_cancel=True)
        if received!=size or sha.hexdigest()!=sha256:raise CloudError('transfer_content_mismatch')
        if stream is not None:
            def flush():stream.flush();os.fsync(stream.fileno());sync_store_directory(path.parent)
            await cloud_work(runtime,flush,drain_cancel=True)
    except BaseException as error:
        logger.warning('cloud_binary_upload_incomplete error_type=%s',type(error).__name__)
        raise
    finally:
        if stream is not None:await cloud_work(runtime,stream.close,drain_cancel=True)
    if not replay:
        def closed_identity():
            with os.fdopen(open_store_readonly(path),'rb') as opened:
                info=os.fstat(opened.fileno())
                if info.st_size!=offset+size:raise CloudError('transfer_length_mismatch')
                return [getattr(info,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')]
        return await cloud_work(runtime,closed_identity,drain_cancel=True)
