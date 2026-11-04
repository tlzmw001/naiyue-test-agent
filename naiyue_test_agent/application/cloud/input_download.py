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
    def __init__(self,service,ident):
        super().__init__(service,None)
        self.input_id=ident;self.key='input:'+ident

    def open(self):
        self.maintenance_lease=self.service.runtime.releases.enter_io()
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
            if self.finished.is_set():self.service.busy.discard(self.key)


async def open_download(service,ident):
    ident=TypeAdapter(ContentId).validate_python(ident)
    if service.closing:raise CloudError('cloud_closing',503)
    key='input:'+ident
    if key in service.busy:raise CloudError('transfer_busy')
    lease=InputDownload(service,ident);service.busy.add(key);service.downloads.add(lease)
    try:
        async with lease.lock:
            await cloud_work(service.runtime,lease.open,drain_cancel=True)
    except BaseException:
        await await_settled(lease.aclose());raise
    return lease
