"""Task-initiated control connections, explicit auth/fences, no locks across sends."""
import asyncio
import logging
import time
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask
from naiyue_test_agent.contracts.cloud_events import canonical_json,digest
from .runner_bootstrap import TaskBootstrap
from .runner_runs import RunnerRuns
from .runner_actions import RunnerActionCommands
from .work import cloud_work

logger=logging.getLogger(__name__)


class Connection:
    def __init__(self,control,websocket,raw,hello):
        self.control,self.websocket,self.raw=control,websocket,raw
        self.task_id=hello.task_id;self.connection_id=str(uuid4())
        self.fence=dict(connection_id=self.connection_id,generation=hello.generation,
            boot_id=hello.boot_id,state_epoch=hello.state_epoch)
        self.lock=asyncio.Lock()

    async def send(self,value):
        async with self.lock:
            await cloud_work(self.control.runtime,lambda:self.control.verify(self))
            if len(canonical_json(value|self.fence).encode())>self.control.runtime.settings.max_frame_bytes:
                raise CloudError('request_too_large',413)
            async with asyncio.timeout(self.control.runtime.settings.control_io_timeout_seconds):
                await self.websocket.send_json(value|self.fence)


class RunnerControl:
    def __init__(self,runtime):
        self.runtime=runtime;self.connections={};self.closing=False
        self.attaching={}
        self.bootstrap=TaskBootstrap(runtime);self.runs=RunnerRuns(self);self.actions=RunnerActionCommands(self)

    def prepare(self,task_id):return self.bootstrap.prepare(task_id)

    def _live_notice(self,action,*args):
        try:action(*args)
        except (CloudError,OSError,ValueError,RuntimeError) as error:
            logger.warning('cloud_live_notification_failed error_type=%s',type(error).__name__)

    def verify(self,connection,frame=None):
        task=self.runtime.credentials.authorize(connection.raw,connection.task_id,'control:connect')
        if task.runner_connection_id!=connection.connection_id:raise CloudError('runner_connection_stale')
        if frame is not None and any(getattr(frame,k)!=v for k,v in connection.fence.items()):raise CloudError('runner_connection_stale')
        return task

    def bind(self,connection,hello):
        r=self.runtime
        with r.gates.hold(hello.task_id):
            task=r.credentials.authorize(connection.raw,hello.task_id,'control:connect')
            expected=dict(project_id=task.project_id,input_id=task.input_id,instance_id=r.instance_id,
                generation=task.generation,boot_id=task.runner_boot_id,state_epoch=task.state_epoch,release_id=task.runner_release_id)
            if any(getattr(hello,k)!=v for k,v in expected.items()):raise CloudError('runner_identity_mismatch')
            if task.input_id is None and hello.file_transfer_version!=1:raise CloudError('template_files_update_required')
            ready=hello.phase=='ready' and task.environment_phase=='backend_ready' and task.input_ready and not task.environment_operation_id
            blockers=[item for item in task.reclaim_blockers if item!='runner_disconnected']
            candidate=CloudTask.model_validate(task.model_dump()|dict(runner_phase=hello.phase,
                runner_model_ids=hello.model_ids,runner_connection_id=connection.connection_id,
                runner_capabilities=hello.capabilities,runner_recovery=hello.recovery.model_dump() if hello.recovery else None,
                reclaim_blockers=blockers,
                lifecycle='ready' if ready else task.lifecycle,revision=task.revision+1,updated_at=time.time(),
                last_quiescent_at=time.time() if ready and not task.active_run_id and not blockers else task.last_quiescent_at))
            r.registry.save_task_cas(candidate,expected_revision=task.revision,expected_generation=task.generation,revoked_at=time.time())
            return candidate

    async def attach(self,websocket,raw,hello):
        if self.closing:raise CloudError('cloud_closing',503)
        if hello.task_id in self.attaching:raise CloudError('runner_connect_busy')
        producer=asyncio.create_task(self._attach(websocket,raw,hello))
        self.attaching[hello.task_id]=producer
        def done(job):
            if self.attaching.get(hello.task_id) is job:self.attaching.pop(hello.task_id)
            if not job.cancelled() and job.exception() is not None:
                logger.warning('cloud_runner_attach_failed error_type=%s',type(job.exception()).__name__)
        producer.add_done_callback(done)
        return await asyncio.shield(producer)

    async def _attach(self,websocket,raw,hello):
        connection=Connection(self,websocket,raw,hello)
        task=await cloud_work(self.runtime,lambda:self.bind(connection,hello))
        previous=self.connections.get(task.task_id);self.connections[task.task_id]=connection
        self._live_notice(self.runtime.observations.connected,task.task_id,connection.connection_id,task.generation)
        if previous is not None:
            try:
                async with asyncio.timeout(self.runtime.settings.control_io_timeout_seconds):
                    await previous.websocket.close(code=1008,reason='runner_connection_replaced')
            except (OSError,RuntimeError,TimeoutError) as error:
                logger.warning('cloud_runner_previous_close_failed error_type=%s',type(error).__name__)
        try:
            await connection.send({'kind':'welcome'})
            if task.active_run_id:
                # The active receipt key is found in trusted metadata, never Runner SQLite.
                command_id=await cloud_work(self.runtime,lambda:self.active_command(task.task_id,task.active_run_id))
                await connection.send(dict(kind='query',command_id=command_id))
            if task.control_operation_id:
                await connection.send(dict(kind='control_query',command_id=task.control_operation_id))
            if task.artifact_operation_id and (await cloud_work(self.runtime,
                    lambda:self.runtime.registry.get_command(task.artifact_operation_id))).operation=='artifact:capture':
                await connection.send(dict(kind='artifact_query',command_id=task.artifact_operation_id))
        except BaseException:
            await self.detach(connection)
            raise
        await self.runtime.mcp.advance(connection)
        return connection

    def active_command(self,task_id,run_id):
        return self.runtime.registry.run_command(task_id,run_id).command_id

    async def receive(self,connection,frame):
        await cloud_work(self.runtime,lambda:self.verify(connection,frame))
        if frame.kind=='observations':
            await self.runtime.observations.receive(connection,frame)
            return
        if frame.kind=='mcp_applied':
            await cloud_work(self.runtime,lambda:self.runtime.mcp.confirm(connection,frame))
        elif frame.kind=='admission_rejected':
            await cloud_work(self.runtime,lambda:self.reject(connection,frame))
        elif frame.kind=='renew':
            b=await cloud_work(self.runtime,lambda:self.bootstrap.renew(connection))
            await connection.send(dict(kind='renewed',token=b.token.get_secret_value(),expires_at=b.expires_at))
        elif frame.kind=='finished':
            await cloud_work(self.runtime,lambda:self.runs.finish(connection,frame))
        elif frame.kind=='artifact_finished':
            await cloud_work(self.runtime,lambda:self.runtime.artifacts.finish(connection,frame))
        elif frame.kind=='artifact_state':
            receipt=await cloud_work(self.runtime,lambda:self.runtime.registry.get_command(frame.command_id))
            if (receipt.operation!='artifact:capture' or receipt.resource_id!=connection.task_id
                    or frame.artifact_id not in (None,receipt.result['command']['artifact_id'])):
                raise CloudError('artifact_identity_conflict')
            if frame.status in ('unknown','not_found'):
                await cloud_work(self.runtime,lambda:self.runtime.artifacts.unconfirmed(connection.task_id,frame.command_id))
        elif frame.kind=='control_finished':
            await cloud_work(self.runtime,lambda:self.actions.finish(connection,frame))
        elif frame.kind=='control_state':
            receipt=await cloud_work(self.runtime,lambda:self.runtime.registry.get_command(frame.command_id))
            if receipt.resource_id!=connection.task_id or receipt.operation not in ('control:stop','control:recover'):raise CloudError('control_identity_mismatch')
            if frame.status in ('unknown','not_found'):
                await cloud_work(self.runtime,lambda:self.actions.unconfirmed(connection.task_id,frame.command_id))
        elif frame.kind=='heartbeat':
            await cloud_work(self.runtime,lambda:self.actions.activity(connection,frame))
            await connection.send({'kind':'welcome'})
        elif frame.kind in ('accepted','state'):
            receipt=await cloud_work(self.runtime,lambda:self.runtime.registry.get_command(frame.command_id))
            if (receipt.resource_id!=connection.task_id or receipt.operation!='run:start'
                    or frame.run_id not in (None,receipt.result['command']['run_id'])):raise CloudError('run_identity_mismatch')
            if frame.status in ('unknown','not_found','interrupted'):
                await cloud_work(self.runtime,lambda:self.runs.unconfirmed(connection.task_id,frame.command_id))
        if frame.kind in ('finished','heartbeat','control_finished','artifact_finished'):
            await self.runtime.mcp.advance(connection)
        self._live_notice(self.runtime.observations.changed,connection.task_id)

    def reject(self,connection,frame):
        r=self.runtime
        with r.gates.hold(connection.task_id):
            task=self.verify(connection,frame);receipt=r.registry.get_command(frame.command_id)
            command=receipt.result.get('command')
            if (receipt.resource_id!=task.task_id or receipt.operation!=frame.operation or not command
                    or digest(command)!=frame.request_digest):raise CloudError('command_id_conflict')
            if receipt.status in ('succeeded','failed'):return
            run=frame.operation=='run:start'
            if (run and task.active_run_id!=command['run_id']
                    or not run and task.artifact_operation_id!=frame.command_id):raise CloudError('command_state_conflict')
            if not run and frame.error_code!='artifact_busy':raise CloudError('artifact_identity_conflict')
            if run and frame.error_code=='artifact_busy':raise CloudError('run_identity_mismatch')
            removed=('active_run','required_history','unknown') if run else ('artifact_capture','artifact_unknown')
            blockers=[x for x in task.reclaim_blockers if x not in removed]
            if not frame.quiescent and 'background_commands' not in blockers:blockers.append('background_commands')
            now=time.time();changes=dict(revision=task.revision+1,updated_at=now,runner_phase=frame.phase,
                reclaim_blockers=blockers,last_quiescent_at=now if frame.quiescent and not blockers else None)
            if run:changes.update(active_run_id=None,last_run_id=receipt.result.get('previous_run_id'))
            else:changes.update(artifact_operation_id=None)
            candidate=task.model_copy(update=changes);related=()
            if run:candidate,related=r.results.rejected(candidate,receipt)
            receipt=receipt.model_copy(update={'status':'failed','http_status':200,'error_code':frame.error_code,
                'result':receipt.result|dict(phase='rejected'),'updated_at':now})
            self.runs.repository.finish(task,candidate,receipt,related=related)

    async def detach(self,connection):
        if self.connections.get(connection.task_id) is not connection:return
        del self.connections[connection.task_id]
        self._live_notice(self.runtime.observations.disconnected,connection.task_id)
        def mark():
            with self.runtime.gates.hold(connection.task_id):
                task=self.runtime.registry.get_task(connection.task_id)
                if task.lifecycle in ('deleting','deleted') or task.runner_connection_id!=connection.connection_id:return None,None,None
                expected_stop=(not task.active_run_id and not task.artifact_operation_id
                    and (task.lifecycle=='stopping' and task.environment_operation_id is not None
                        and self.runtime.registry.get_command(task.environment_operation_id).operation=='environment:stop_environment'
                        or task.lifecycle=='stopped' and task.environment_phase=='absent'))
                self.runtime.registry.save_task_cas(task.model_copy(update={'revision':task.revision+1,
                    'runner_connection_id':None,'runner_phase':'unconfigured' if expected_stop else 'disconnected','last_quiescent_at':None,
                    'reclaim_blockers':task.reclaim_blockers if expected_stop else list(dict.fromkeys([*task.reclaim_blockers,'runner_disconnected']))}),expected_revision=task.revision,
                    expected_generation=task.generation,revoked_at=time.time())
                return task.active_run_id,task.artifact_operation_id,task.control_operation_id
        active,artifact,action=await cloud_work(self.runtime,mark)
        if active:
            command=await cloud_work(self.runtime,lambda:self.active_command(connection.task_id,active))
            await cloud_work(self.runtime,lambda:self.runs.unconfirmed(connection.task_id,command))
        if action:await cloud_work(self.runtime,lambda:self.actions.unconfirmed(connection.task_id,action))
        if artifact and (await cloud_work(self.runtime,lambda:self.runtime.registry.get_command(artifact))).operation=='artifact:capture':
            await cloud_work(self.runtime,lambda:self.runtime.artifacts.unconfirmed(connection.task_id,artifact))

    async def aclose(self):
        self.closing=True
        await self.runtime.artifacts.producers.aclose()
        pending=[*self.attaching.values()]
        if pending:await asyncio.gather(*(asyncio.shield(p) for p in pending),return_exceptions=True)
        await self.runs.producers.aclose()
        await self.actions.producers.aclose()
        for connection in list(self.connections.values()):
            await connection.websocket.close(code=1001,reason='coordinator_closing')
