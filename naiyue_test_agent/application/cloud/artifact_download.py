"""A response lease pins its verified file and drains reads before closing it."""
import asyncio
import hashlib
import logging
import os

from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .artifact_store import file_identity
from .work import cloud_work

logger=logging.getLogger(__name__)


class ArtifactDownload:
    def __init__(self,service,task_id):
        self.service=service;self.stream=None;self.record=None;self.identity=None
        self.task_id=task_id;self.start=0;self.length=None;self.partial=False
        self.lock=asyncio.Lock();self.finished=asyncio.Event();self.reserved=False

    def open(self,task_id,artifact_id):
        self.service.runtime.task_io._enter(task_id);self.reserved=True
        self.service.get(task_id,artifact_id)
        with self.service.runtime.gates.hold(task_id):
            transfer=self.service.repository.get(task_id,artifact_id)
            self.stream=self.service.store.open(transfer)
            self.record=transfer.artifact;self.identity=transfer.blob_identity

    def range(self,header,if_range):
        import re
        from starlette.exceptions import HTTPException
        size=self.record.size;self.length=size
        if header is None or if_range is not None and if_range!='"'+self.record.sha256+'"':return
        match=re.fullmatch(r'bytes=([0-9]*)-([0-9]*)',header)
        def invalid():raise HTTPException(416,detail='invalid_range',headers={'Content-Range':'bytes */'+str(size)})
        if not match or not size:invalid()
        left,right=match.groups()
        if not left and not right or len(left)>20 or len(right)>20:invalid()
        if left:
            start=int(left);end=min(int(right),size-1) if right else size-1
            if start>=size or end<start:invalid()
        else:
            suffix=int(right)
            if suffix==0:invalid()
            start=max(0,size-suffix);end=size-1
        self.start=start;self.length=end-start+1;self.partial=True;self.stream.seek(start)

    async def chunks(self):
        size=0;sha=hashlib.sha256()
        while True:
            async with self.lock:
                if self.finished.is_set():raise CloudError('task_not_available')
                remaining=(self.length if self.length is not None else self.record.size)-size
                data=await cloud_work(self.service.runtime,lambda:self.stream.read(min(remaining,self.service.limits.io_bytes)) if remaining else b'',drain_cancel=True)
                if not data:
                    if (size!=(self.length if self.length is not None else self.record.size)
                            or not self.partial and sha.hexdigest()!=self.record.sha256
                            or file_identity(os.fstat(self.stream.fileno()))!=self.identity):raise CloudError('artifact_content_mismatch')
            if not data:break
            size+=len(data);sha.update(data)
            if size>self.record.size:raise CloudError('artifact_content_mismatch')
            yield data

    async def aclose(self):
        async with self.lock:
            if self.finished.is_set():return
            closed=self.stream is None
            try:
                if self.stream is not None:await cloud_work(self.service.runtime,self.stream.close,drain_cancel=True)
                closed=True
            except BaseException as error:
                logger.error('cloud_artifact_download_close_failed error_type=%s',type(error).__name__)
                raise
            finally:
                if closed or getattr(self.stream,'closed',False):
                    if self.reserved:self.service.runtime.task_io._leave(self.task_id);self.reserved=False
                    self.service.downloads.discard(self);self.finished.set()


async def open_download(service,task_id,artifact_id):
    if service.closing:raise CloudError('cloud_closing',503)
    lease=ArtifactDownload(service,task_id);service.downloads.add(lease)
    try:
        async with lease.lock:
            await cloud_work(service.runtime,lambda:lease.open(task_id,artifact_id),drain_cancel=True)
    except BaseException:
        await await_settled(lease.aclose());raise
    return lease
