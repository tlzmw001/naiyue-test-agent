"""Owner capture commands and task-only upload; immutable service-side artifacts."""
from contextlib import contextmanager
import asyncio
import time
from uuid import uuid4
from pydantic import TypeAdapter

from naiyue_test_agent.contracts.cloud_artifacts import CaptureRequest,CaptureArtifact,ArtifactRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask,CommandReceipt,TaskId,UUIDText
from naiyue_test_agent.contracts.cloud_events import digest,canonical_json
from naiyue_test_agent.storage.cloud.artifacts import ArtifactRepository
from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository
from .artifact_store import ArtifactStore
from .byte_stream import upload_headers,receive_bytes
from .producers import CommandProducers
from .work import cloud_work


class CloudArtifacts:
    def __init__(self,runtime):
        self.runtime=runtime;self.limits=runtime.settings.artifact_limits
        self.repository=ArtifactRepository(runtime.database);self.commands=LifecycleRepository(runtime.database)
        self.store=ArtifactStore(runtime.settings.data_dir,self.limits)
        self.producers=CommandProducers('cloud_artifact_producer_failed')
        self.busy=set();self.uploads=set();self.downloads=set();self.closing=False

    @contextmanager
    def exclusive(self,task_id,artifact_id):
        key=(task_id,artifact_id)
        if key in self.busy:raise CloudError('artifact_busy')
        self.busy.add(key)
        try:yield
        finally:self.busy.remove(key)

    async def capture(self,task_id,value):
        request=CaptureRequest.model_validate(value)
        if self.closing or self.runtime.control.closing:raise CloudError('cloud_closing',503)
        fingerprint=digest(dict(operation='artifact:capture',task_id=task_id,request=request.model_dump()))
        return await asyncio.shield(self.producers.submit(request.command_id,fingerprint,
            lambda:self._capture(task_id,request,fingerprint)))

    def reserve(self,task_id,request,fingerprint,connection):
        r=self.runtime
        with r.gates.hold(task_id):
            try:old=r.registry.get_command(request.command_id)
            except CloudError as error:
                if error.code!='command_not_found':raise
            else:
                if old.request_digest!=fingerprint:raise CloudError('command_id_conflict')
                return old,False
            r.resources.ensure()
            task=r.registry.get_task(task_id)
            if task.revision!=request.expected_revision or task.generation!=request.expected_generation:raise CloudError('task_revision_conflict')
            if ('artifacts:capture' not in task.runner_capabilities):raise CloudError('runner_capability_unavailable')
            if (connection is None or task.runner_connection_id!=connection.connection_id or task.environment_operation_id
                    or task.environment_phase!='backend_ready' or not task.input_ready or task.active_run_id
                    or task.artifact_operation_id or task.control_operation_id or task.reclaim_blockers or task.runner_phase not in ('ready','waiting_model')):
                raise CloudError('artifact_busy')
            r.control.verify(connection)
            command=CaptureArtifact(command_id=request.command_id,artifact_id=str(uuid4()),relative_name=request.relative_name)
            if len(canonical_json(dict(kind='artifact_capture',command=command.model_dump())|connection.fence).encode())>r.settings.max_frame_bytes:
                raise CloudError('request_too_large',413)
            now=time.time();candidate=CloudTask.model_validate(task.model_dump()|dict(revision=task.revision+1,
                artifact_operation_id=request.command_id,reclaim_blockers=['artifact_capture'],last_quiescent_at=None,updated_at=now))
            receipt=CommandReceipt(command_id=request.command_id,resource_id=task_id,operation='artifact:capture',
                expected_generation=task.generation,request_digest=fingerprint,status='running',http_status=202,
                result=dict(phase='dispatched',command=command.model_dump(),connection_id=connection.connection_id),created_at=now,updated_at=now)
            return self.commands.reserve(task,candidate,receipt,r.settings)

    async def _capture(self,task_id,request,fingerprint):
        async with self.runtime.task_io.use(task_id):
            connection=self.runtime.control.connections.get(task_id)
            receipt,fresh=await cloud_work(self.runtime,lambda:self.reserve(task_id,request,fingerprint,connection))
            if fresh:
                try:await connection.send(dict(kind='artifact_capture',command=receipt.result['command']))
                except (CloudError,OSError,RuntimeError,TimeoutError):
                    await cloud_work(self.runtime,lambda:self.unconfirmed(task_id,request.command_id))
                    return await cloud_work(self.runtime,lambda:self.runtime.registry.get_command(request.command_id))
            return receipt

    def unconfirmed(self,task_id,command_id):
        r=self.runtime
        with r.gates.hold(task_id):
            task=r.registry.get_task(task_id)
            if task.lifecycle in ('deleting','deleted'):return
            receipt=r.registry.get_command(command_id)
            if receipt.status in ('succeeded','failed'):return
            if task.artifact_operation_id!=command_id:raise CloudError('artifact_identity_conflict')
            candidate=task.model_copy(update={'revision':task.revision+1,
                'reclaim_blockers':list(dict.fromkeys([*task.reclaim_blockers,'artifact_unknown']))})
            receipt=receipt.model_copy(update={'status':'unknown','error_code':'artifact_unconfirmed','updated_at':time.time()})
            self.commands.finish(task,candidate,receipt)

    def _authorize(self,raw,task_id,artifact_id,record):
        r=self.runtime;task=r.credentials.authorize(raw,task_id,'artifacts:write')
        if record.artifact_id!=artifact_id:raise CloudError('artifact_identity_conflict')
        receipt=r.registry.get_command(record.command_id)
        if receipt.operation=='result:capture':return r.results.authorize_artifact(task,record,receipt)
        if (record.task_id!=task_id or record.project_id!=task.project_id or record.input_id!=task.input_id
                or record.artifact_id!=artifact_id or record.generation!=receipt.expected_generation
                or record.generation>task.generation or receipt.operation!='artifact:capture' or receipt.resource_id!=task_id
                or receipt.result['command']!={k:getattr(record,k) for k in ('command_id','artifact_id','relative_name')}
                or receipt.status=='failed' or receipt.status!='succeeded' and task.artifact_operation_id!=record.command_id):
            raise CloudError('artifact_identity_conflict')
        if receipt.status=='succeeded':
            stored=self.repository.get(task_id,artifact_id)
            if stored.state!='available' or stored.artifact!=record:raise CloudError('artifact_identity_conflict')
        return task

    def prepare(self,raw,task_id,artifact_id,value):
        with self.runtime.task_io.hold(task_id):
            record=ArtifactRecord.model_validate(value)
            with self.runtime.gates.hold(task_id):
                self._authorize(raw,task_id,artifact_id,record)
                if record.size>self.limits.max_file_bytes:raise CloudError('artifact_budget',413)
                try:existing=self.repository.get(task_id,artifact_id)
                except CloudError as error:
                    if error.code!='artifact_not_found':raise
                else:
                    if existing.artifact!=record:raise CloudError('artifact_identity_conflict')
                    return existing
                self.runtime.resources.ensure(additional_bytes=record.size)
                transfer=self.repository.prepare(record,self.limits)
            if transfer.state!='available':self.store.staging(record)
            return transfer

    async def upload(self,raw,task_id,artifact_id,request):
        async with self.runtime.task_io.use(task_id):
            if self.closing:raise CloudError('cloud_closing',503)
            current=asyncio.current_task();self.uploads.add(current)
            try:
                async with asyncio.timeout(self.limits.transfer_timeout_seconds):
                    with self.exclusive(task_id,artifact_id):
                        def begin():
                            with self.runtime.gates.hold(task_id):
                                previous=self.repository.get(task_id,artifact_id)
                                authorized=self._authorize(raw,task_id,artifact_id,previous.artifact)
                                upload_headers(request,previous.artifact.size,previous.artifact.sha256)
                                if previous.state not in ('pending','uploading','uploaded'):raise CloudError('artifact_not_writable')
                                replay=previous.state=='uploaded'
                                if not replay:self.runtime.resources.ensure(additional_bytes=previous.artifact.size)
                                value=previous if replay else self.repository.save(previous,previous.model_copy(update={'state':'uploading'}))
                                return value,replay,authorized.generation
                        transfer,replay,generation=await cloud_work(self.runtime,begin,drain_cancel=True)
                        path=await cloud_work(self.runtime,lambda:self.store.staging(transfer.artifact)/'bytes',drain_cancel=True)
                        await receive_bytes(self.runtime,request,path=path,size=transfer.artifact.size,sha256=transfer.artifact.sha256,
                            io_bytes=self.limits.io_bytes,replay=replay)
                        def finish():
                            with self.runtime.gates.hold(task_id):
                                task=self.runtime.registry.get_task(task_id)
                                if (task.generation!=generation or task.artifact_operation_id!=transfer.artifact.command_id
                                        or task.lifecycle in ('deleted','deleting')):raise CloudError('artifact_identity_conflict')
                                return transfer if replay else self.repository.save(transfer,transfer.model_copy(update={'state':'uploaded'}))
                        return await cloud_work(self.runtime,finish,drain_cancel=True)
            except TimeoutError:raise CloudError('transfer_timeout',408) from None
            finally:self.uploads.remove(current)

    def commit(self,raw,task_id,artifact_id):
        with self.runtime.task_io.hold(task_id):
            with self.runtime.gates.hold(task_id):
                transfer=self.repository.get(task_id,artifact_id);self._authorize(raw,task_id,artifact_id,transfer.artifact)
                if transfer.state=='available':return self._get(task_id,artifact_id)
                if transfer.state!='uploaded':raise CloudError('artifact_not_uploaded')
            identity=self.store.publish(transfer.artifact)
            with self.runtime.gates.hold(task_id):
                self._authorize(raw,task_id,artifact_id,transfer.artifact)
                self.repository.available(transfer,identity)
                return transfer.artifact

    async def commit_owned(self,raw,task_id,artifact_id):
        async def producer():
            with self.exclusive(task_id,artifact_id):
                return await cloud_work(self.runtime,lambda:self.commit(raw,task_id,artifact_id),drain_cancel=True)
        key='commit:'+task_id+':'+artifact_id
        return await asyncio.shield(self.producers.submit(key,digest(key),producer))

    def get(self,task_id,artifact_id):
        TypeAdapter(TaskId).validate_python(task_id);TypeAdapter(UUIDText).validate_python(artifact_id)
        with self.runtime.gates.hold(task_id):
            artifact=self.repository.get(task_id,artifact_id).artifact
            receipt=self.runtime.registry.get_command(artifact.command_id)
            if receipt.operation=='result:capture' and receipt.result['phase']!='available':raise CloudError('artifact_not_found',404)
            return self._get(task_id,artifact_id)

    def _get(self,task_id,artifact_id):
        self.runtime.available_task(task_id)
        transfer=self.repository.get(task_id,artifact_id)
        if transfer.state!='available':raise CloudError('artifact_not_found',404)
        stream=self.store.open(transfer);stream.close()
        return transfer.artifact

    def finish(self,connection,frame):
        r=self.runtime
        with r.gates.hold(connection.task_id):
            task=r.control.verify(connection,frame);receipt=r.registry.get_command(frame.command_id)
            if (receipt.operation!='artifact:capture' or receipt.resource_id!=task.task_id
                    or receipt.result['command']['artifact_id']!=frame.artifact_id):raise CloudError('artifact_identity_conflict')
            if receipt.status in ('succeeded','failed'):return
            if task.artifact_operation_id!=frame.command_id or not frame.quiescent or frame.phase not in ('ready','waiting_model'):
                raise CloudError('artifact_not_quiescent')
            artifact=None
            if frame.status=='available':
                artifact=self._get(task.task_id,frame.artifact_id)
                if artifact.command_id!=frame.command_id or artifact.generation!=receipt.expected_generation:raise CloudError('artifact_identity_conflict')
            elif frame.status!='failed' or not frame.error_code:raise CloudError('artifact_unconfirmed')
            else:
                try:self.repository.get(task.task_id,frame.artifact_id)
                except CloudError as error:
                    if error.code!='artifact_not_found':raise
                else:raise CloudError('artifact_upload_unconfirmed')
            blockers=[x for x in task.reclaim_blockers if x not in ('artifact_capture','artifact_unknown')]
            now=time.time();candidate=task.model_copy(update={'revision':task.revision+1,'artifact_operation_id':None,
                'runner_phase':frame.phase,'reclaim_blockers':blockers,'last_quiescent_at':now if not blockers else None,'updated_at':now})
            receipt=receipt.model_copy(update={'status':'succeeded' if artifact else 'failed','http_status':200,
                'result':receipt.result|dict(phase='finished',artifact=artifact.model_dump() if artifact else None),
                'error_code':frame.error_code,'updated_at':now})
            self.commands.finish(task,candidate,receipt)

    async def open_download(self,task_id,artifact_id):
        from .artifact_download import open_download
        return await open_download(self,task_id,artifact_id)

    async def aclose(self):
        self.closing=True
        await self.producers.aclose()
        if self.uploads:await asyncio.gather(*(asyncio.shield(t) for t in list(self.uploads)),return_exceptions=True)
        if self.downloads:await asyncio.gather(*(d.finished.wait() for d in list(self.downloads)))
