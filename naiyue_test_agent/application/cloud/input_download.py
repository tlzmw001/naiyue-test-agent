"""Committed input archives share bounded range streaming and pinned FD custody."""
import os
from dataclasses import dataclass

from pydantic import TypeAdapter
from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError,ContentId
from naiyue_test_agent.storage.files import open_store_readonly
from .artifact_download import ArtifactDownload
from .artifact_store import file_identity
from .work import cloud_work


@dataclass(frozen=True)
class InputArchive:
    sha256:str
    size:int
    relative_name:str


class InputDownload(ArtifactDownload):
    def __init__(self,service,ident,task_id=None,use_id=None):
        super().__init__(service,task_id)
        self.use_id=use_id
        self.input_id=ident;self.reservation=service.reading(ident)

    def open(self):
        self.maintenance_lease=self.service.runtime.releases.enter_io()
        if self.task_id is not None:
            self.service.runtime.task_io._enter(self.task_id);self.reserved=True
        if self.use_id is not None:self.service.uses.check(self.use_id,self.input_id,'recover')
        package=self.service.get_input(self.input_id)
        self.record=InputArchive(package.archive_sha256,package.size,
            self.input_id+('.tar.gz' if package.codec=='gzip' else '.tar'))
        self.identity=package.archive_identity
        self.stream=os.fdopen(open_store_readonly(self.service.root/'inputs'/self.input_id/'archive'),'rb')
        if file_identity(os.fstat(self.stream.fileno()))!=self.identity:
            raise CloudError('input_archive_missing')

    async def aclose(self):
        try:await super().aclose()
        finally:
            if self.finished.is_set() and self.reservation is not None:
                reservation,self.reservation=self.reservation,None
                reservation.__exit__(None,None,None)


async def open_download(service,ident,*,task_id=None,use_id=None):
    ident=TypeAdapter(ContentId).validate_python(ident)
    if service.closing:raise CloudError('cloud_closing',503)
    if len(service.downloads)>=service.limits.max_downloads:raise CloudError('input_download_capacity',429)
    lease=InputDownload(service,ident,task_id,use_id)
    lease.reservation.__enter__()
    service.downloads.add(lease)
    try:
        async with lease.lock:
            await cloud_work(service.runtime,lease.open,drain_cancel=True)
    except BaseException:
        await await_settled(lease.aclose());raise
    return lease
