"""One task's durable control producer; never replays a business invocation."""
import asyncio
import json
import logging

from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.application.errors import SessionBusyError
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger=logging.getLogger(__name__)


class RunnerActions:
    def __init__(self,kernel):
        self.kernel=kernel;self.task=None;self.command_id=None;self.closing=False

    @property
    def busy(self):return self.task is not None and not self.task.done()

    async def execute(self,command):
        value=await self.accept(command)
        if self.command_id==command.command_id and self.busy:return await asyncio.shield(self.task)
        return value

    async def reject(self,command,error):
        value,_=await run_blocking(self.kernel.journal.admit_control,command)
        if value['status'] in ('completed','failed'):return value
        return await run_blocking(self.kernel.journal.finish_control,command.command_id,status='failed',
            error_code=error.code,phase=self.kernel.phase,quiescent=self.kernel.session_quiet(),
            recovery=self.kernel.recovery_info.model_dump() if self.kernel.recovery_info else None)

    async def accept(self,command):
        k=self.kernel;j=k.journal
        old=await run_blocking(j.control_lookup,command.command_id)
        if old is not None:
            if digest(old['command'])!=digest(command.model_dump()):raise CloudError('command_id_conflict')
            # The required recovery transaction can commit before installing
            # its in-memory actor. Retry only that local tail of this same run.
            restore_committed=(old['status']=='completed' and command.kind=='recover'
                and k.phase=='blocked' and k.last_run_id==command.run_id
                and old['generation']==k.bootstrap.generation)
            if (old['status'] in ('completed','failed') and not restore_committed) or self.busy:return old
        if self.closing or self.busy:raise CloudError('runner_control_busy')
        if k.catalog is None:raise CloudError('model_unavailable')
        latest=await run_blocking(j.latest)
        active=k.active_task is not None and not k.active_task.done()
        if command.run_id is not None and (latest is None or command.run_id!=latest['run_id']
                or command.target_command_id!=latest['command_id']):raise CloudError('run_identity_mismatch')
        if active and command.run_id is None:raise CloudError('run_identity_mismatch')
        if (hasattr(k,'artifacts') and k.artifacts.active_task is not None
                and not k.artifacts.active_task.done()):raise CloudError('runner_busy')
        if hasattr(k,'artifacts') and any(v.status in ('capturing','captured','unknown') for v in await run_blocking(k.artifacts.journal.list)):
            raise CloudError('artifact_busy')
        if command.kind=='recover':
            if active:raise CloudError('runner_busy')
            info=await k.inspect_recovery()
            if info is None or info.kind not in ('interrupted_execution','pending_approval','background_unknown','ready'):
                raise CloudError('session_recovery_blocked')
            if command.checkpoint_id!=info.checkpoint_id:
                row=await run_blocking(k.store.sessions.get,k.bootstrap.task_id)
                snapshot=await k.agent.aget_state({'configurable':{'thread_id':row['thread_id']}})
                marker=json.loads((snapshot.metadata or {}).get('naiyue_recovery','null'))
                if old is None or not marker or marker.get('source_checkpoint_id')!=command.checkpoint_id:
                    raise CloudError('checkpoint_mismatch')
            if k.previous_activity and k.previous_activity['generation']>=k.bootstrap.generation:
                raise CloudError('old_environment_not_fenced')
        before=k.phase;k.phase='blocked'
        try:value,_=await run_blocking(j.admit_control,command)
        except BaseException:
            k.phase=before;raise
        self.command_id=command.command_id
        self.task=asyncio.create_task(self._run(command))
        def observed(done):
            if not done.cancelled() and done.exception() is not None:
                logger.error('runner_control_failed error_type=%s',type(done.exception()).__name__)
        self.task.add_done_callback(observed)
        return value

    async def _run(self,command):
        k=self.kernel
        try:
            if command.kind=='stop':
                session=k.sessions.require(k.bootstrap.task_id) if k.sessions.has_session(k.bootstrap.task_id) else None
                if session is not None and session.is_busy():
                    try:session.controls.request_scope_stop(run_id=command.run_id,reason='user')
                    except SessionBusyError:
                        logger.info('runner_stop_waiting_for_finalize')
                if k.active_task is not None and not k.active_task.done():
                    try:await await_settled(k.active_task)
                    except (Exception,asyncio.CancelledError) as error:
                        logger.warning('runner_stopped_execution_error error_type=%s',type(error).__name__)
                await k.sessions.background_tasks.stop_session(k.bootstrap.task_id)
                if not k._session_quiet():raise CloudError('runner_stop_unconfirmed')
                latest=await run_blocking(k.journal.latest)
                k.phase='ready' if latest is None or latest['status'] in ('completed','stopped','recovered') else 'blocked'
                k.recovery_info=await k.inspect_recovery() if k.phase=='blocked' else None
                await run_blocking(k.journal.mark_activity,False)
                value=await run_blocking(k.journal.finish_control,command.command_id,phase=k.phase,quiescent=True,
                    recovery=k.recovery_info.model_dump() if k.recovery_info else None)
            else:
                # Seal all old in-memory authorities before assembling from the
                # same durable state. No old graph is invoked by recovery.
                k.sessions.stop_loaded_sessions()
                await k.sessions.background_tasks.aclose()
                await k.assemble()
                await k.bind_observations()
                info=await k.inspect_recovery()
                if info.kind in ('ready','background_unknown'):
                    await k.persistence.arestore(k.bootstrap.task_id,k.agent)
                    value=await run_blocking(k.journal.control_lookup,command.command_id)
                    if value['status']!='completed':
                        value=await run_blocking(k.journal.recover_ready,command.command_id)
                else:
                    await k.persistence.arecover(k.bootstrap.task_id,k.agent,checkpoint_id=info.checkpoint_id,action='continue')
                    value=await run_blocking(k.journal.control_lookup,command.command_id)
                k.phase='ready';k.recovery_info=None;k.previous_activity=None
            return value
        except BaseException:
            k.phase='blocked'
            try:await await_settled(run_blocking(k.journal.unknown_control,command.command_id))
            except Exception as error:logger.error('runner_control_save_unconfirmed error_type=%s',type(error).__name__)
            raise

    async def aclose(self):
        self.closing=True
        if self.busy:await await_settled(self.task)
