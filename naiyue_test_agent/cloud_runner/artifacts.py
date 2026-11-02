"""A fixed task freezes one file once; Coordinator receives only immutable bytes."""
import asyncio
import hashlib
import logging
import os
import stat
import time
from pydantic import TypeAdapter

from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.application.cloud.ownership import private_directory
from naiyue_test_agent.cloud_files.paths import source_handle,identity
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText
from naiyue_test_agent.storage.files import open_store_file,open_store_readonly,sync_store_directory
from naiyue_test_agent.storage.runner_artifacts import RunnerArtifactJournal
from .state import read_json,write_json

logger=logging.getLogger(__name__)


class RunnerArtifacts:
    def __init__(self,kernel,root,state):
        self.kernel,self.root,self.state=kernel,root,state
        self.journal=RunnerArtifactJournal(kernel.store.database)
        self.active_task=None;self.closing=False
        self._restore_phase='ready' if kernel.catalog else 'waiting_model'
        for value in self.journal.list():
            if value.status=='unknown':
                target=self._directory('artifacts',value.command.artifact_id)
                if target.exists():
                    metadata=ArtifactRecord.model_validate(read_json(target/'artifact.json'))
                    b=kernel.bootstrap
                    if (any(getattr(metadata,k)!=getattr(value.command,k) for k in ('command_id','artifact_id','relative_name'))
                            or any(getattr(metadata,k)!=getattr(b,k) for k in ('task_id','project_id','input_id'))
                            or metadata.generation>b.generation):
                        raise CloudError('artifact_spool_identity_mismatch')
                    size,sha=self._hash(target/'bytes')
                    if (size,sha)!=(metadata.size,metadata.sha256):raise CloudError('artifact_spool_identity_mismatch')
                    sync_store_directory(target.parent)
                    self.journal.save(value.model_copy(update={'status':'captured','artifact':metadata,'error_code':None}))

    def _directory(self,kind,ident):
        TypeAdapter(UUIDText).validate_python(ident)
        base=self.state/kind;base.mkdir(mode=0o700,exist_ok=True);private_directory(base)
        return base/ident

    def blob(self,ident):
        target=self._directory('artifacts',ident);private_directory(target)
        return target/'bytes'

    def _hash(self,path):
        sha=hashlib.sha256();size=0
        with os.fdopen(open_store_readonly(path),'rb') as f:
            while data:=f.read(self.kernel.bootstrap.artifact_limits.io_bytes):
                size+=len(data)
                if size>self.kernel.bootstrap.artifact_limits.max_file_bytes:raise CloudError('artifact_budget',413)
                sha.update(data)
        return size,sha.hexdigest()

    def _spooled_bytes(self):
        total=0
        for value in self.journal.list():
            for kind in ('artifacts','artifact-staging'):
                directory=self._directory(kind,value.command.artifact_id)
                if not directory.exists():continue
                private_directory(directory)
                path=directory/'bytes'
                if not path.exists() and not path.is_symlink():continue
                with os.fdopen(open_store_readonly(path),'rb') as stream:total+=os.fstat(stream.fileno()).st_size
        return total

    def _admit(self,command):
        count=sum(value.error_code!='artifact_capacity' or any(
            os.path.lexists(self.state/kind/value.command.artifact_id) for kind in ('artifacts','artifact-staging'))
            for value in self.journal.list())
        rejection='artifact_capacity' if count>=self.kernel.bootstrap.artifact_limits.max_artifacts else None
        return self.journal.admit(command,rejection=rejection)

    def _freeze(self,command):
        self.kernel.authority.resolve(self.kernel.bootstrap.project_id)
        target=self.root/command.relative_name;before=target.lstat()
        if not stat.S_ISREG(before.st_mode):raise CloudError('artifact_type_unsupported')
        limits=self.kernel.bootstrap.artifact_limits
        if before.st_size>limits.max_file_bytes:raise CloudError('artifact_budget',413)
        used=self._spooled_bytes()
        if used+before.st_size>limits.max_cache_bytes:raise CloudError('artifact_capacity')
        stage=self._directory('artifact-staging',command.artifact_id)
        stage.mkdir(mode=0o700);private_directory(stage)
        sha=hashlib.sha256();size=0
        with source_handle(self.root,command.relative_name,identity(before)) as (source,opened):
            if opened is None:raise CloudError('artifact_type_unsupported')
            with os.fdopen(open_store_file(stage/'bytes',exclusive_create=True),'wb') as output:
                while data:=source.read(limits.io_bytes):
                    size+=len(data)
                    if size>limits.max_file_bytes:raise CloudError('artifact_budget',413)
                    if used+size>limits.max_cache_bytes:raise CloudError('artifact_capacity')
                    sha.update(data);output.write(data)
                if size!=opened.st_size:raise CloudError('artifact_source_changed')
                output.flush();os.fsync(output.fileno())
        self.kernel.authority.resolve(self.kernel.bootstrap.project_id)
        b=self.kernel.bootstrap
        record=ArtifactRecord(**command.model_dump(),task_id=b.task_id,project_id=b.project_id,input_id=b.input_id,
            generation=b.generation,size=size,sha256=sha.hexdigest(),captured_at=time.time())
        write_json(stage/'artifact.json',record.model_dump());sync_store_directory(stage)
        destination=self._directory('artifacts',command.artifact_id)
        if destination.exists() or destination.is_symlink():raise CloudError('artifact_spool_identity_mismatch')
        os.rename(stage,destination);sync_store_directory(destination.parent)
        return record

    async def accept(self,command):
        previous=await run_blocking(self.journal.get,command.command_id)
        if previous is not None:
            if previous.command!=command:raise CloudError('command_id_conflict')
            return previous
        unsettled=await run_blocking(self.kernel.journal.has_unsettled)
        if (self.closing or self.kernel.phase not in ('ready','waiting_model')
                or not self.kernel.session_quiet() or unsettled):
            raise CloudError('artifact_busy')
        self._restore_phase=self.kernel.phase;self.kernel.phase='blocked'
        try:value=await run_blocking(self._admit,command)
        except BaseException:
            self.kernel.phase=self._restore_phase;raise
        if value.status=='failed':
            self.kernel.phase=self._restore_phase
            return value
        self.active_task=asyncio.create_task(self._capture(value))
        def finished(done):
            if not done.cancelled() and done.exception() is not None:
                logger.error('runner_artifact_capture_failed error_type=%s',type(done.exception()).__name__)
        self.active_task.add_done_callback(finished)
        return value

    async def capture(self,command):
        value=await self.accept(command)
        if value.status=='capturing':return await asyncio.shield(self.active_task)
        return value

    async def _capture(self,value):
        try:record=await run_blocking(self._freeze,value.command)
        except (CloudError,OSError) as error:
            logger.warning('runner_artifact_source_failed error_type=%s',type(error).__name__)
            published=self._directory('artifacts',value.command.artifact_id).exists()
            status='unknown' if published else 'failed'
            value=value.model_copy(update={'status':status,'error_code':getattr(error,'code','artifact_source_unavailable')})
            await run_blocking(self.journal.save,value)
            if status=='failed':self.kernel.phase=self._restore_phase
            return value
        try:return await run_blocking(self.journal.save,value.model_copy(update={'status':'captured','artifact':record}))
        except BaseException:
            await await_settled(run_blocking(self.journal.save,value.model_copy(update={'status':'unknown','error_code':'artifact_capture_unconfirmed'})))
            raise

    def available(self,value):
        value=self.journal.save(value.model_copy(update={'status':'available'}))
        self.kernel.phase=self._restore_phase
        return value

    async def aclose(self):
        self.closing=True
        if self.active_task:await asyncio.gather(asyncio.shield(self.active_task),return_exceptions=True)
