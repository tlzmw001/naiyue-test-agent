"""Owner commands are durable before dispatch; history, not WS claims, settles them."""
import asyncio
import logging
import time
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_events import digest,canonical_json
from naiyue_test_agent.contracts.cloud_runner import StartRun,RunCommand
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask,CommandReceipt
from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository
from .work import cloud_work
from .producers import CommandProducers

logger=logging.getLogger(__name__)


class RunnerRuns:
    def __init__(self,control):
        self.control,self.runtime=control,control.runtime
        self.repository=LifecycleRepository(self.runtime.database)
        self.producers=CommandProducers('cloud_run_dispatch_failed')

    async def start(self,task_id,value):
        request=StartRun.model_validate(value)
        fingerprint=digest(dict(task_id=task_id,request=request.model_dump()))
        if self.control.closing:raise CloudError('cloud_closing',503)
        return await asyncio.shield(self.producers.submit(request.command_id,fingerprint,
            lambda:self._start(task_id,request,fingerprint)))

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
            if task.revision!=request.expected_revision or task.generation!=request.expected_generation:raise CloudError('task_revision_conflict')
            if task.template_snapshot is not None:r.releases.validate_template(task.template_snapshot)
            if task.runner_phase=='waiting_model':raise CloudError('model_unavailable')
            if (task.lifecycle!='ready' or task.runner_phase!='ready' or not task.runner_connection_id
                    or task.runner_connection_id!=connection_id
                    or task.environment_operation_id or task.control_operation_id or task.active_run_id
                    or any(x not in ('background_commands','result_pending') for x in task.reclaim_blockers)):raise CloudError('runner_not_ready')
            if request.model_id not in task.runner_model_ids:raise CloudError('model_unavailable')
            r.models.validate_thinking(task,request.model_id,request.thinking_level)
            try:r.credentials.authorize(raw,task_id,'control:connect')
            except CloudError:raise CloudError('runner_not_ready') from None
            attachments=[]
            if request.attachment_ids:
                if task.template_snapshot is None or task.template_snapshot.file_transfer_version!=1:raise CloudError('template_files_update_required')
                if len(set(request.attachment_ids))!=len(request.attachment_ids):raise CloudError('invalid_request',422)
                for aid in request.attachment_ids:
                    if aid not in task.attachments:raise CloudError('attachment_not_found',404)
                    attachments.append(task.attachments[aid])
            if attachments:
                additional=0
                for item in attachments:
                    package=r.inputs.get_input(item.input_id)
                    additional+=package.size+sum(entry.size for entry in package.manifest.entries)
                r.resources.ensure(additional_bytes=additional)
            now=time.time();run_id=uuid4().hex
            previous=task;task,updates=r.results.next_run(task,run_id)
            plan,result_receipt=r.results.reserve_run(task,request.command_id,run_id)
            services=None
            if task.network_id:
                from naiyue_test_agent.storage.cloud.networks import NetworkRepository
                from naiyue_test_agent.contracts.cloud_networks import service_context
                endpoint=r.settings.networks.get(task.network_id)
                if endpoint is None:raise CloudError('cloud_network_not_configured',409)
                services=service_context(NetworkRepository(r.database).read(task.network_id),endpoint)
            command=RunCommand(attachments=attachments,services=services,command_id=request.command_id,run_id=run_id,message=request.message,model_id=request.model_id,
                thinking_level=request.thinking_level,result_plan=plan)
            frame=dict(kind='command',command=command.model_dump(),connection_id=connection_id,
                generation=task.generation,boot_id=task.runner_boot_id,state_epoch=task.state_epoch)
            if len(canonical_json(frame).encode())>r.settings.max_frame_bytes:raise CloudError('request_too_large',413)
            candidate=CloudTask.model_validate(task.model_dump()|dict(active_run_id=run_id,last_run_id=run_id,
                title=task.title or ' '.join(request.message.split())[:80],
                result_ids=[*task.result_ids,plan.result_id],reclaim_blockers=['active_run','required_history'],last_quiescent_at=None,revision=task.revision+1,updated_at=now))
            receipt=CommandReceipt(command_id=request.command_id,resource_id=task_id,operation='run:start',
                expected_generation=task.generation,request_digest=fingerprint,status='running',http_status=202,
                result=dict(phase='dispatched',command=command.model_dump(),connection_id=task.runner_connection_id,
                    previous_run_id=previous.last_run_id,previous_pending_result_id=previous.pending_result_id),created_at=now,updated_at=now)
            # Reuse the existing atomic task+receipt CAS. Environment-only limits
            # in this repository apply solely to environment:start.
            return self.repository.reserve(previous,candidate,receipt,r.settings,related=(result_receipt,),related_updates=updates)

    async def _start(self,task_id,request,fingerprint):
        live=self.control.connections.get(task_id)
        connection_id=live.connection_id if live else None
        raw=live.raw if live else None
        receipt,fresh=await cloud_work(self.runtime,lambda:self.reserve(task_id,request,fingerprint,connection_id,raw))
        if not fresh:return receipt
        connection=self.control.connections.get(task_id)
        try:
            if connection is None or connection.connection_id!=receipt.result['connection_id']:raise CloudError('runner_disconnected')
            await connection.send(dict(kind='command',command=receipt.result['command']))
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
            blockers=list(dict.fromkeys([*task.reclaim_blockers,'unknown']))
            candidate=task.model_copy(update={'revision':task.revision+1,'reclaim_blockers':blockers,'runner_phase':'blocked'})
            receipt=receipt.model_copy(update={'status':'unknown','error_code':'run_unconfirmed','updated_at':time.time()})
            self.repository.finish(task,candidate,receipt)

    def finish(self,connection,frame):
        r=self.runtime
        with r.gates.hold(connection.task_id):
            task=self.control.verify(connection,frame)
            receipt=r.registry.get_command(frame.command_id)
            if (receipt.operation!='run:start' or receipt.resource_id!=task.task_id
                    or receipt.result['command']['run_id']!=frame.run_id):raise CloudError('run_identity_mismatch')
            if receipt.status in ('succeeded','failed'):return
            event=r._history(task.task_id).event(frame.terminal_seq)
            if (not event or event['epoch']!=frame.state_epoch or event['run_id']!=frame.run_id
                    or event['source_generation']!=receipt.expected_generation or event['kind']!='history_record'
                    or event['payload'].get('record')!='run_finished' or event['payload'].get('command_id')!=frame.command_id
                    or event['payload'].get('status')!=frame.status):raise CloudError('run_history_unconfirmed')
            if frame.status not in ('completed','stopped','failed','interrupted'):raise CloudError('run_not_finished')
            if task.active_run_id!=frame.run_id:raise CloudError('run_identity_mismatch')
            now=time.time()
            phase='ready' if frame.status in ('completed','stopped') else 'blocked'
            blockers=['control_operation'] if task.control_operation_id else []
            if not frame.quiescent:blockers.append('background_commands')
            if phase=='blocked':blockers.append('recovery_required')
            candidate=task.model_copy(update={'revision':task.revision+1,'active_run_id':None,'reclaim_blockers':blockers,
                'runner_phase':phase,'last_quiescent_at':now if frame.quiescent and not blockers else None,'updated_at':now})
            receipt=receipt.model_copy(update={'status':'succeeded' if frame.status in ('completed','stopped') else 'failed',
                'http_status':200,'error_code':None if frame.status in ('completed','stopped') else 'run_failed',
                'result':receipt.result|dict(phase='finished',terminal_seq=frame.terminal_seq),'updated_at':now})
            candidate,related=r.results.terminal(candidate,receipt,dict(kind='run',epoch=event['epoch'],seq=frame.terminal_seq,outcome=frame.status))
            self.repository.finish(task,candidate,receipt,related=related)
