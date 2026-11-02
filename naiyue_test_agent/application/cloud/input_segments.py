"""Reconcile a fixed archive against durable segment facts before extending it."""
import hashlib
import os

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_readonly,sync_store_directory


def identity(info):return [getattr(info,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')]


def recover(service,ident):
    value=service.transfer(ident)
    if value.state in ('committed','cancelled'):return value
    path=service.staging(ident)/'archive'
    try:fd=open_store_readonly(path)
    except FileNotFoundError:
        target=service.root/'inputs'/value.input_id
        if value.state=='uploaded' and (target.exists() or target.is_symlink()):
            package=service._package(value.input_id)
            if (package.size,package.archive_sha256,package.codec)!=(value.size,value.archive_sha256,value.codec):
                raise CloudError('input_record_mismatch')
            return value
        if value.offset:raise CloudError('transfer_confirmed_bytes_missing') from None
        return value
    with os.fdopen(fd,'rb') as stream:
        before=identity(os.fstat(stream.fileno()))
        if value.archive_identity==before and before[2]==value.offset:return value
        if before[2]<value.offset:raise CloudError('transfer_confirmed_bytes_missing')
        offset=0
        for segment in value.segments:
            if segment.offset!=offset or segment.size>value.chunk_bytes:raise CloudError('transfer_ledger_invalid')
            remaining=segment.size;sha=hashlib.sha256()
            while remaining:
                chunk=stream.read(min(remaining,service.limits.io_bytes))
                if not chunk:raise CloudError('transfer_confirmed_bytes_missing')
                sha.update(chunk);remaining-=len(chunk)
            if sha.hexdigest()!=segment.sha256:raise CloudError('transfer_confirmed_bytes_changed')
            offset+=segment.size
        if offset!=value.offset:raise CloudError('transfer_ledger_invalid')
        if identity(os.fstat(stream.fileno()))!=before:raise CloudError('transfer_confirmed_bytes_changed')
    # Service-owned file, same transfer reservation; no task can mutate this path.
    from naiyue_test_agent.storage.files import open_store_file
    with os.fdopen(open_store_file(path),'r+b') as stream:
        if identity(os.fstat(stream.fileno()))!=before:raise CloudError('transfer_confirmed_bytes_changed')
        stream.truncate(value.offset);stream.flush();os.fsync(stream.fileno())
        after=identity(os.fstat(stream.fileno()))
    sync_store_directory(path.parent)
    return service.repository.save(value,value.model_copy(update={'archive_identity':after}))


async def status(service,ident):
    from .work import cloud_work
    if ident in service.busy:return await cloud_work(service.runtime,lambda:service.transfer(ident))
    if service.closing:raise CloudError('cloud_closing',503)
    def reconciled():
        value=recover(service,ident)
        if value.state=='uploaded' and not (service.staging(ident)/'archive').exists():
            service.commit(ident)
            return service.transfer(ident)
        return value
    return await service.perform(ident,reconciled)
