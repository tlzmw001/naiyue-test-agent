"""Delete only a fenced, physically verified shared input generation."""
from contextlib import ExitStack
import logging
import os
import sqlite3
import stat

from pydantic import TypeAdapter
from naiyue_test_agent.contracts.cloud_tasks import CloudError, ContentId
from naiyue_test_agent.storage.cloud.input_reclamation import InputReclamation
from .resource_disk import open_directory, mount_id
from .artifact_store import file_identity
from .work import cloud_work

logger=logging.getLogger(__name__)
FILES=('archive','input.json','manifest.json','selection.json')


def _identity(info):
    return [info.st_dev,info.st_ino]


def remove(runtime,repo,cid,index,item,package):
    expected=item.get('identity')
    with ExitStack() as stack:
        def opened(name,parent=None):
            fd=open_directory(name,dir_fd=parent)
            stack.callback(os.close,fd)
            return fd
        root=opened(runtime.settings.data_dir)
        base=opened('inputs',root)
        mount=mount_id(root)
        if mount_id(base)!=mount:raise CloudError('input_cleanup_mount')
        ident=TypeAdapter(ContentId).validate_python(item['input_id'])
        try:
            fd=opened(ident,base)
        except FileNotFoundError:
            if not expected:raise CloudError('input_cleanup_identity_unavailable') from None
            if _identity(os.fstat(base))!=expected['base']:raise CloudError('input_cleanup_identity_changed')
            os.fsync(base)
            return 0,False
        if mount_id(fd)!=mount:
            raise CloudError('input_cleanup_mount')
        directory=_identity(os.fstat(fd))
        base_identity=_identity(os.fstat(base))
        facts={}
        with os.scandir(fd) as entries:
            for entry in entries:
                name=entry.name
                info=entry.stat(follow_symlinks=False)
                if name not in FILES or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_dev!=directory[0]:
                    raise CloudError('input_cleanup_unmanaged_entry')
                facts[name]=file_identity(info)
        if expected is None:
            if set(facts)!=set(FILES) or facts['archive']!=package['archive_identity']:
                raise CloudError('input_cleanup_identity_changed')
            expected=dict(base=base_identity,directory=directory,files=facts)
            repo.evidence(cid,index,expected)
        if (expected['base']!=base_identity or expected['directory']!=directory
                or any(expected['files'].get(name)!=fact for name,fact in facts.items())):
            raise CloudError('input_cleanup_identity_changed')
        removed=0
        for name,fact in facts.items():
            if file_identity(os.stat(name,dir_fd=fd,follow_symlinks=False))!=fact:
                raise CloudError('input_cleanup_identity_changed')
            os.unlink(name,dir_fd=fd)
            removed+=fact[2]
        os.fsync(fd)
        if _identity(os.stat(ident,dir_fd=base,follow_symlinks=False))!=directory:
            raise CloudError('input_cleanup_identity_changed')
        os.rmdir(ident,dir_fd=base)
        os.fsync(base)
        return removed,len(facts)==len(FILES)


async def reclaim(runtime,receipt):
    repo=InputReclamation(runtime.database)
    cid=receipt.command_id
    cleanup=receipt.result.get('input_cleanup')
    items=cleanup['candidates'] if cleanup else []
    if not items:
        if cleanup and cleanup['state']=='completed':return receipt
        return await cloud_work(runtime,lambda:repo.empty(receipt))
    for index,item in enumerate(items):
        if item['state']=='reclaimed':continue
        key='input:'+item['input_id']
        if key in runtime.inputs.busy:
            receipt=await cloud_work(runtime,lambda:repo.mark(cid,index,'retained','input_io_active'))
            continue
        try:
            with runtime.inputs.exclusive(key):
                lease=await cloud_work(runtime,lambda:runtime.releases.enter_io(
                    continuation=cid,input_id=item['input_id']),drain_cancel=True)
                try:
                    package=await cloud_work(runtime,lambda:repo.claim(cid,index),drain_cancel=True)
                    if package is not None:
                        removed,complete=await cloud_work(runtime,lambda:remove(runtime,repo,cid,index,item,package),drain_cancel=True)
                        receipt=await cloud_work(runtime,lambda:repo.finish(cid,index,removed,complete),drain_cancel=True)
                finally:
                    await cloud_work(runtime,lambda:runtime.releases.leave_io(lease),drain_cancel=True)
        except (CloudError,OSError,sqlite3.Error) as error:
            reason=error.code if isinstance(error,CloudError) else 'input_cleanup_io_failed'
            if reason=='cloud_maintenance':
                logger.info('input_cleanup_paused command_id=%s input_id=%s',cid,item['input_id'])
                continue
            logger.warning('input_cleanup_failed input_id=%s code=%s',item['input_id'],reason)
            detail=dict(code=reason,errno=getattr(error,'errno',None),winerror=getattr(error,'winerror',None))
            try:
                receipt=await cloud_work(runtime,lambda:repo.mark(cid,index,'failed',detail),drain_cancel=True)
            except (CloudError,sqlite3.Error,OSError):
                logger.exception('input_cleanup_receipt_failed command_id=%s',cid)
                return receipt
    return await cloud_work(runtime,lambda:runtime.registry.get_command(cid))
