"""Owner stop/recover admission; only confirmed history can release control custody."""
import asyncio
import logging
import time

from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_runner import TaskControl,RunnerActionCommand
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt
from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository
from .producers import CommandProducers
from .work import cloud_work

logger=logging.getLogger(__name__)


class RunnerActionCommands:
    def __init__(self,control):
        self.control,self.runtime=control,control.runtime
        self.repository=LifecycleRepository(self.runtime.database)
        self.producers=CommandProducers('cloud_control_dispatch_failed')

    async def command(self,task_id,value):
        request=TaskControl.model_validate(value)
        fingerprint=digest(dict(task_id=task_id,request=request.model_dump()))
        if self.control.closing:raise CloudError('cloud_closing',503)
        return await asyncio.shield(self.producers.submit(request.command_id,fingerprint,
            lambda:self._command(task_id,request,fingerprint)))

    def reserve(self,task_id,request,fingerprint,connection_id,raw):
        r=self.runtime
        with r.gates.hold(task_id):
            try:old=r.registry.get_command(request.command_id)
            except CloudError as error:
                if error.code!='command_not_found':raise
            else:
                if old.request_digest!=fingerprint:raise CloudError('command_id_conflict')
                return old,False
            task=r.registry.get_task(task_id)
            if task.generation!=request.expected_generation or task.revision!=request.expected_revision:raise CloudError('task_revision_conflict')
            if (task.lifecycle not in ('ready','starting') or not task.runner_connection_id
                    or task.runner_connection_id!=connection_id or task.control_operation_id
                    or task.environment_operation_id or task.artifact_operation_id):raise CloudError('task_busy')
            if 'control:'+request.kind not in task.runner_capabilities:raise CloudError('control_unavailable')
            r.credentials.authorize(raw,task_id,'control:connect')
            target=None
            if request.run_id is not None:
                if request.run_id!=task.last_run_id and request.run_id!=task.active_run_id:raise CloudError('run_identity_mismatch')
                target=r.registry.run_command(task_id,request.run_id).command_id
            elif task.active_run_id:raise CloudError('run_identity_mismatch')
            command=RunnerActionCommand(command_id=request.command_id,kind=request.kind,run_id=request.run_id,
                target_command_id=target,checkpoint_id=request.checkpoint_id)
            now=time.time()
            candidate=task.model_copy(update={'revision':task.revision+1,'control_operation_id':request.command_id,
                'reclaim_blockers':list(dict.fromkeys([*task.reclaim_blockers,'control_operation'])),
                'last_quiescent_at':None,'updated_at':now})
            receipt=CommandReceipt(command_id=request.command_id,resource_id=task_id,operation='control:'+request.kind,
                expected_generation=task.generation,request_digest=fingerprint,status='running',http_status=202,
                result=dict(command=command.model_dump(),connection_id=connection_id,phase='dispatched'),created_at=now,updated_at=now)
            return self.repository.reserve(task,candidate,receipt,r.settings)

    async def _command(self,task_id,request,fingerprint):
        live=self.control.connections.get(task_id)
        receipt,fresh=await cloud_work(self.runtime,lambda:self.reserve(task_id,request,fingerprint,
            live.connection_id if live else None,live.raw if live else None))
        # An explicit owner retry may finish a previously failed local tail.
        # Only safe stop/recover commands are resent, with their original ID.
        if not fresh and receipt.status in ('succeeded','failed'):return receipt
        connection=self.control.connections.get(task_id)
        try:
            task=await cloud_work(self.runtime,lambda:self.runtime.registry.get_task(task_id))
            if (connection is None or task.generation!=receipt.expected_generation
                    or task.control_operation_id!=receipt.command_id):raise CloudError('runner_disconnected')
            await connection.send(dict(kind='control_command',command=receipt.result['command']))
        except (CloudError,OSError,RuntimeError,TimeoutError):
            await cloud_work(self.runtime,lambda:self.unconfirmed(task_id,receipt.command_id))
            return await cloud_work(self.runtime,lambda:self.runtime.registry.get_command(receipt.command_id))
        return receipt

    def unconfirmed(self,task_id,command_id):
        r=self.runtime
        with r.gates.hold(task_id):
            task=r.registry.get_task(task_id)
            if task.lifecycle in ('deleting','deleted'):return
            receipt=r.registry.get_command(command_id)
            if receipt.status in ('succeeded','failed'):return
            candidate=task.model_copy(update={'revision':task.revision+1,'runner_phase':'blocked',
                'reclaim_blockers':list(dict.fromkeys([*task.reclaim_blockers,'control_unknown']))})
            self.repository.finish(task,candidate,receipt.model_copy(update={'status':'unknown','error_code':'control_unconfirmed','updated_at':time.time()}))

    def finish(self,connection,frame):
        r=self.runtime
        with r.gates.hold(connection.task_id):
            task=self.control.verify(connection,frame);receipt=r.registry.get_command(frame.command_id)
            if receipt.resource_id!=task.task_id or receipt.operation not in ('control:stop','control:recover'):raise CloudError('control_identity_mismatch')
            if receipt.status in ('succeeded','failed'):return
            if task.control_operation_id!=frame.command_id:raise CloudError('control_identity_mismatch')
            event=r._history(task.task_id).event(frame.terminal_seq)
            expected={k:getattr(frame,k) for k in ('command_id','status','phase','quiescent','error_code','recovery')}
            if frame.recovery:expected['recovery']=frame.recovery.model_dump()
            command=receipt.result['command']
            if (not event or event['epoch']!=frame.state_epoch or event['source_generation']!=receipt.expected_generation
                    or event['run_id']!=(command['run_id'] or frame.command_id.replace('-',''))
                    or event['kind']!='history_record' or event['payload']!=dict(record='control_finished',**expected)):
                raise CloudError('control_history_unconfirmed')
            if frame.status=='completed' and not frame.quiescent:raise CloudError('control_not_quiescent')
            blockers=[x for x in task.reclaim_blockers if x not in ('control_operation','control_unknown')]
            active=task.active_run_id;related=[]
            if receipt.operation=='control:recover' and frame.status=='completed' and frame.phase=='ready':
                original=r.registry.get_command(command['target_command_id'])
                if original.resource_id!=task.task_id or original.operation!='run:start':raise CloudError('run_identity_mismatch')
                now=time.time()
                related=[original.model_copy(update={'status':'failed' if original.status in ('accepted','running','unknown') else original.status,
                    'http_status':200,'error_code':'run_recovered_unconfirmed' if original.status in ('accepted','running','unknown') else original.error_code,
                    'result':original.result|dict(recovery_command_id=frame.command_id),'updated_at':now})]
                active=None;blockers=[x for x in blockers if x not in ('active_run','required_history','unknown','runner_disconnected','recovery_required','background_commands')]
            elif frame.phase=='ready' and active is None and frame.status=='completed':
                blockers=[x for x in blockers if x=='artifact_unknown']
            if frame.phase=='blocked' and 'recovery_required' not in blockers:blockers.append('recovery_required')
            now=time.time()
            candidate=task.model_copy(update={'revision':task.revision+1,'control_operation_id':None,'active_run_id':active,
                'runner_phase':frame.phase,'runner_recovery':frame.recovery.model_dump() if frame.recovery else None,
                'reclaim_blockers':blockers,'last_quiescent_at':now if frame.quiescent and not blockers and not active else None,'updated_at':now})
            updated=receipt.model_copy(update={'status':'succeeded' if frame.status=='completed' else 'failed','http_status':200,
                'error_code':frame.error_code,'result':receipt.result|dict(phase='finished',terminal_seq=frame.terminal_seq),'updated_at':now})
            self.repository.finish(task,candidate,updated,related=related)

    def activity(self,connection,frame):
        if frame.quiescent is None:return
        r=self.runtime
        with r.gates.hold(connection.task_id):
            task=self.control.verify(connection,frame)
            if frame.run_id!=task.last_run_id:return
            if task.active_run_id or task.control_operation_id or task.artifact_operation_id or task.environment_operation_id:
                if frame.recovery:
                    now=time.time()
                    r.registry.save_task_cas(task.model_copy(update={'revision':task.revision+1,'runner_recovery':frame.recovery.model_dump()}),
                        expected_revision=task.revision,expected_generation=task.generation,revoked_at=now)
                return
            blockers=[x for x in task.reclaim_blockers if x!='background_commands']
            if not frame.quiescent:blockers.append('background_commands')
            now=time.time()
            r.registry.save_task_cas(task.model_copy(update={'revision':task.revision+1,'reclaim_blockers':blockers,
                'runner_phase':frame.phase or task.runner_phase,'runner_recovery':frame.recovery.model_dump() if frame.recovery else None,
                'last_quiescent_at':now if frame.quiescent and not blockers else None,'updated_at':now}),
                expected_revision=task.revision,expected_generation=task.generation,revoked_at=now)
