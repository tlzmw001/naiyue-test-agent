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
        self.task_id=task_id
        self.lock=asyncio.Lock();self.finished=asyncio.Event()

    def open(self,task_id,artifact_id):
        with self.service.runtime.task_io.hold(task_id):
            self.service.get(task_id,artifact_id)
            transfer=self.service.repository.get(task_id,artifact_id)
            self.stream=self.service.store.open(transfer)
            self.record=transfer.artifact;self.identity=transfer.blob_identity

    async def chunks(self):
        size=0;sha=hashlib.sha256()
        while True:
            async with self.lock:
                if self.finished.is_set():raise CloudError('task_not_available')
                data=await cloud_work(self.service.runtime,lambda:self.stream.read(self.service.limits.io_bytes),drain_cancel=True)
                if not data:
                    if ((size,sha.hexdigest())!=(self.record.size,self.record.sha256)
                            or file_identity(os.fstat(self.stream.fileno()))!=self.identity):raise CloudError('artifact_content_mismatch')
            if not data:break
            size+=len(data);sha.update(data)
            if size>self.record.size:raise CloudError('artifact_content_mismatch')
            yield data

    async def aclose(self):
        async with self.lock:
            if self.finished.is_set():return
            try:
                if self.stream is not None:await cloud_work(self.service.runtime,self.stream.close,drain_cancel=True)
            except BaseException as error:
                logger.error('cloud_artifact_download_close_failed error_type=%s',type(error).__name__)
                raise
            finally:
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
