"""Owner input admission, strict commit and ready-package ownership."""
from contextlib import contextmanager
import asyncio
import logging
import os
import time
from uuid import uuid4

from pydantic import TypeAdapter
from naiyue_test_agent.contracts.cloud_inputs import CreateTransfer,InputTransfer,InputPackage,CreateTask
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt,CloudTask,ContentId,UUIDText
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.cloud.inputs import InputRepository
from naiyue_test_agent.storage.files import open_store_readonly,open_store_file,sync_store_directory
from .ownership import private_directory
from naiyue_test_agent.cloud_files.archive import validate_package
from .work import cloud_work

logger=logging.getLogger(__name__)


class CloudInputs:
    def __init__(self,runtime):
        self.runtime=runtime;self.repository=InputRepository(runtime.database)
        self.root=runtime.settings.data_dir;self.limits=runtime.settings.input_limits;self.busy=set()
        self.jobs=set();self.uploads=set();self.downloads=set();self.closing=False

    async def open_download(self,ident):
        from .input_download import open_download
        return await open_download(self,ident)

    async def perform(self,ident,action):
        if self.closing:raise CloudError('cloud_closing',503)
        if ident in self.busy:raise CloudError('transfer_busy')
        self.busy.add(ident)
        async def producer():
            try:
                transfer=await cloud_work(self.runtime,lambda:self.transfer(ident))
                with self.exclusive('input:'+transfer.input_id):
                    return await cloud_work(self.runtime,action,drain_cancel=True)
            finally:self.busy.remove(ident)
        job=asyncio.create_task(producer());self.jobs.add(job)
        def finished(done):
            self.jobs.discard(done)
            if not done.cancelled() and done.exception() is not None and not isinstance(done.exception(),CloudError):
                logger.error('cloud_input_producer_failed error_type=%s',type(done.exception()).__name__)
        job.add_done_callback(finished)
        return await asyncio.shield(job)

    async def aclose(self):
        self.closing=True
        for lease in tuple(self.downloads):await lease.aclose()
        if self.uploads:await asyncio.gather(*(asyncio.shield(job) for job in list(self.uploads)),return_exceptions=True)
        if self.jobs:
            results=await asyncio.gather(*(asyncio.shield(job) for job in list(self.jobs)),return_exceptions=True)
            for result in results:
                if isinstance(result,BaseException) and not isinstance(result,CloudError):raise result

    @contextmanager
    def exclusive(self,key):
        if key in self.busy:raise CloudError('transfer_busy')
        self.busy.add(key)
        try:yield
        finally:self.busy.remove(key)

    def staging(self,ident):
        ident=TypeAdapter(UUIDText).validate_python(ident)
        base=self.root/'transfers';created=not base.exists();base.mkdir(mode=0o700,exist_ok=True);private_directory(base)
        if created:sync_store_directory(base.parent)
        path=base/ident;created=not path.exists();path.mkdir(mode=0o700,exist_ok=True);private_directory(path)
        if created:sync_store_directory(path.parent)
        return path

    async def sweep_expired(self,*,now=None):
        if self.closing:return []
        cutoff=(time.time() if now is None else now)-self.limits.staging_ttl_seconds
        rows=await cloud_work(self.runtime,self.repository.transfers)
        expired=[]
        for value in rows:
            if value.state in ('committed','cancelled') or value.updated_at>cutoff or value.transfer_id in self.busy:continue
            def expire():
                current=self.transfer(value.transfer_id)
                if current.state in ('committed','cancelled') or current.updated_at>cutoff:return None
                return self.cancel(current.transfer_id)
            try:
                result=await self.perform(value.transfer_id,expire)
                if result is not None:expired.append(value.transfer_id)
            except CloudError as error:
                if error.code not in ('transfer_busy','input_already_committed'):
                    logger.warning('cloud_input_expiry_failed transfer_id=%s code=%s',value.transfer_id,error.code)
            except OSError as error:
                logger.error('cloud_input_expiry_failed transfer_id=%s error_type=%s',value.transfer_id,type(error).__name__)
        return expired

    def transfer(self,ident):return self.repository.transfer(TypeAdapter(UUIDText).validate_python(ident))

    def get_input(self,ident):
        ident=TypeAdapter(ContentId).validate_python(ident);package=self.repository.input(ident)
        return self._package(ident,expected=package)

    def _package(self,ident,*,expected=None):
        directory=self.root/'inputs'/ident;private_directory(directory)
        with os.fdopen(open_store_readonly(directory/'input.json'),'rb') as stream:
            package=InputPackage.model_validate_json(stream.read(self.limits.max_metadata_bytes*3+1))
        if expected is not None and package!=expected:raise CloudError('input_record_mismatch')
        if package.input_id!=ident or package.manifest.input_id!=ident:raise CloudError('input_record_mismatch')
        with os.fdopen(open_store_readonly(directory/'archive'),'rb') as stream:
            fact=os.fstat(stream.fileno())
            identity=[getattr(fact,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')]
            if fact.st_size!=package.size or package.archive_identity!=identity:raise CloudError('input_archive_missing')
        for name,expected_bytes in (('manifest.json',package.manifest.canonical),('selection.json',package.selection.canonical)):
            with os.fdopen(open_store_readonly(directory/name),'rb') as stream:
                if stream.read(self.limits.max_metadata_bytes+1)!=expected_bytes:raise CloudError('input_record_mismatch')
        # Byte/hash verification runs at commit and inside the task. Management
        # files remain service-owned; hot reads do not hash a large archive again.
        return package

    def create(self,value):
        request=CreateTransfer.model_validate(value)
        fingerprint=digest({'operation':'input:transfer','instance_id':self.runtime.instance_id,'request':request.model_dump(exclude={'command_id'})})
        try:old=self.runtime.registry.get_command(request.command_id)
        except CloudError as error:
            if error.code!='command_not_found':raise
        else:
            if old.request_digest!=fingerprint:raise CloudError('command_id_conflict')
            return old
        if request.size>self.limits.max_archive_bytes:raise CloudError('input_budget',413)
        cached = False
        try:self.get_input(request.input_id)
        except CloudError as error:
            if error.code!='input_not_found':raise
        else:cached = True
        self.runtime.resources.ensure(additional_bytes=0 if cached else request.size)
        now=time.time();transfer=InputTransfer(**request.model_dump(),transfer_id=str(uuid4()),state='pending',offset=0,
            chunk_bytes=self.limits.chunk_bytes,updated_at=now)
        receipt=CommandReceipt(command_id=request.command_id,resource_id=transfer.transfer_id,operation='input:transfer',
            request_digest=fingerprint,status='succeeded',http_status=201,result={'transfer':transfer.model_dump()},created_at=now,updated_at=now)
        return self.repository.create(transfer,receipt,self.limits,self.runtime.settings.max_receipts)

    def commit(self,ident):
        transfer=self.transfer(ident)
        if transfer.state=='committed':return self.get_input(transfer.input_id)
        if transfer.state!='uploaded':raise CloudError('transfer_not_uploaded')
        staging=self.staging(ident);archive=staging/'archive'
        target=self.root/'inputs'/transfer.input_id
        if not archive.exists() and target.exists():
            existing=self._package(transfer.input_id)
            if (existing.size!=transfer.size or existing.archive_sha256!=transfer.archive_sha256 or existing.codec!=transfer.codec):
                raise CloudError('input_record_mismatch')
            archive=target/'archive'
        manifest,selection=validate_package(archive,transfer.codec,self.limits,expected_id=transfer.input_id,
            expected_archive_sha256=transfer.archive_sha256,expected_size=transfer.size)
        fact=archive.stat(follow_symlinks=False)
        package=InputPackage(input_id=transfer.input_id,archive_sha256=transfer.archive_sha256,size=transfer.size,codec=transfer.codec,manifest=manifest,selection=selection,
            archive_identity=[getattr(fact,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')])
        base=self.root/'inputs';base.mkdir(mode=0o700,exist_ok=True);private_directory(base);target=base/package.input_id
        if target.exists() or target.is_symlink():
            existing=self._package(package.input_id)
            if existing.manifest!=manifest or existing.selection!=selection:raise CloudError('input_record_mismatch')
            # If another transfer committed the same I with different compression,
            # its verified canonical package remains the source of archive facts.
            package=existing
        else:
            for name,data in (('manifest.json',manifest.canonical),('selection.json',selection.canonical),('input.json',package.model_dump_json(by_alias=True).encode())):
                fd=open_store_file(staging/name)
                with os.fdopen(fd,'wb') as stream:
                    stream.write(data);stream.truncate();stream.flush();os.fsync(stream.fileno())
            sync_store_directory(staging);os.rename(staging,target);sync_store_directory(base)
        return self.repository.commit(transfer,package)

    def cancel(self,ident):
        transfer=self.transfer(ident)
        if transfer.state=='committed':raise CloudError('input_already_committed')
        if transfer.state=='cancelled':return transfer
        target=self.root/'inputs'/transfer.input_id
        if transfer.state=='uploaded' and (target.exists() or target.is_symlink()):
            try:self.repository.input(transfer.input_id)
            except CloudError as error:
                if error.code!='input_not_found':raise
                self.commit(ident)
                raise CloudError('input_already_committed') from None
        path=self.staging(ident)
        # Only fixed managed files; no recursive removal of task/user content.
        for name in ('archive','manifest.json','selection.json','input.json'):
            file=path/name
            try:
                fd=open_store_readonly(file);os.close(fd);file.unlink()
            except FileNotFoundError:continue
        path.rmdir();sync_store_directory(path.parent)
        return self.repository.save(transfer,transfer.model_copy(update={'state':'cancelled'}))

    def create_task(self,value):
        request=CreateTask.model_validate(value)
        fingerprint=digest({'operation':'task:create','instance_id':self.runtime.instance_id,'request':request.model_dump(exclude={'command_id'})})
        try:old=self.runtime.registry.get_command(request.command_id)
        except CloudError as error:
            if error.code!='command_not_found':raise
        else:
            if old.request_digest!=fingerprint:raise CloudError('command_id_conflict')
            return old
        self.runtime.resources.ensure()
        try:self.get_input(request.input_id)
        except CloudError as error:
            if error.code=='input_not_found':raise CloudError('input_not_ready') from None
            raise
        template=self.runtime.settings.templates.get(request.template_id)
        if template is None:raise CloudError('template_unavailable')
        now=time.time();task=CloudTask(task_id=uuid4().hex,project_id=request.project_id,title=request.title,input_id=request.input_id,
            template_id=template.template_id,runner_release_id=template.runner_release_id,state_schema=1,generation=1,revision=1,
            lifecycle='creating',created_at=now,updated_at=now,template_snapshot=template,template_digest=template.fingerprint)
        receipt=CommandReceipt(command_id=request.command_id,resource_id=task.task_id,operation='task:create',request_digest=fingerprint,
            status='succeeded',http_status=201,result={'task':task.model_dump()},created_at=now,updated_at=now)
        return self.repository.create_task(task,receipt,self.runtime.settings.max_receipts,self.runtime.settings.max_retained_environments)
