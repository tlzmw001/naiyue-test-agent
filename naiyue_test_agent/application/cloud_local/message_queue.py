"""A server-owned pending turn uses result history, never a browser's WS state."""
import asyncio,logging
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cloud_message_queue import QueueRepository

logger=logging.getLogger(__name__)


class MessageQueue:
    def __init__(self,manager):self.manager=manager;self.jobs={};self.closing=False

    async def submit(self,domain,bid,sid,request):
        with self.manager.operation(),domain.owner.operation():
            binding=await run_blocking(domain.repo.binding,bid,sid)
            run=(binding.get('cloud') or {}).get('active_run_id')
            value=await run_blocking(QueueRepository(domain.repo.database).reserve,bid,sid,request,run)
            self.launch(domain,value);return value

    async def read(self,domain,bid,sid):
        with self.manager.operation(),domain.owner.operation():
            value=await run_blocking(QueueRepository(domain.repo.database).read,bid,sid)
            if value is not None:self.launch(domain,value)
            return value

    def launch(self,domain,value):
        key=(domain.project_id,value['command_id'])
        if self.closing or key in self.jobs or value['phase'] not in ('waiting','dispatching'):return
        async def work():
            with domain.owner.operation():
                current=value
                while current['phase'] in ('waiting','dispatching'):
                    current=await self.advance(domain,current)
                    if current['phase'] in ('waiting','dispatching'):await asyncio.sleep(self.manager.settings.queue_poll_seconds)
        job=asyncio.create_task(work());self.jobs[key]=job;domain.own(job)
        job.add_done_callback(lambda done:self.jobs.pop(key,None))

    async def advance(self,domain,value):
        repo=QueueRepository(domain.repo.database);bid=value['binding_id'];sid=value['local_session_id'];cid=value['command_id']
        value=await run_blocking(domain.repo.operation,cid,sid)
        if value['phase'] not in ('waiting','dispatching'):return value
        try:
            if value['phase']=='waiting':
                binding=await self.manager.read(domain,bid,sid);task=binding.get('cloud')
                if not task or not binding['retained']:raise CloudError('cloud_task_unavailable')
                if task['runner_phase'] in ('blocked','unknown') or any('unknown' in x or 'approval' in x for x in task['reclaim_blockers']):
                    raise CloudError('cloud_queue_previous_unconfirmed')
                if task['active_run_id']:
                    if task['active_run_id']!=value['source_run_id']:raise CloudError('cloud_queue_run_changed')
                    return value
                if task['last_run_id']!=value['source_run_id']:raise CloudError('cloud_queue_run_changed')
                async with self.manager.connection(binding) as client:
                    rows=await run_blocking(client.request,'GET','/tasks/'+binding['task_id']+'/results',params={'offset':0,'limit':20})
                result=next((row for row in rows if row['source_run_id']==value['source_run_id']),None)
                if result and result['phase'] in ('failed','unknown','abandoned','superseded','deleted'):raise CloudError('cloud_queue_result_unavailable')
                if result is None or result.get('outcome') is None:return value
                if result['outcome']!='completed':raise CloudError('cloud_queue_previous_not_completed')
                if result['phase'] in ('failed','unknown','abandoned','superseded','deleted'):raise CloudError('cloud_queue_result_unavailable')
                if (result['phase']!='available' or task['runner_phase']!='ready' or task['control_operation_id']
                        or task['environment_operation_id'] or task['artifact_operation_id']
                        or any(x not in ('background_commands',) for x in task['reclaim_blockers'])):return value
                if 'mcp:refresh' in task.get('runner_capabilities', ()):
                    status=await self.manager.view(domain,bid,sid,'mcp',{})
                    if status['status'] in ('refreshing','waiting_run','waiting_runner'):return value
                for aid in value['request'].get('attachment_ids',[]):
                    item=binding.get('local_attachments',{}).get(aid)
                    if item is None or item['phase'] in ('failed','cancelled'):raise CloudError('attachment_not_ready')
                    if item['phase'] not in ('ready','attached'):return value
                request=value['request']|dict(command_id=value['message_command_id'],expected_binding_revision=binding['revision'],draft_revision=None)
                value=await run_blocking(repo.update,cid,sid,'dispatching',expected=('waiting',),dispatch_request=request)
            operation=await self.manager.commands.submit(domain,bid,'message',value['dispatch_request'])
            if operation['phase'] in ('reserved','fixed') and not any(step.get('sent') for step in operation['steps'].values()):
                self.manager.commands.launch(domain,operation)
            key=(domain.project_id,value['message_command_id'])
            if key in self.manager.commands.flights:await asyncio.shield(self.manager.commands.flights[key][1])
            operation=await self.manager.commands.reconcile(domain,operation['command_id'],sid)
            if operation['phase'] in ('accepted','succeeded') or operation['phase']=='failed' and operation['steps']['primary'].get('receipt'):
                return await run_blocking(repo.update,cid,sid,'sent',expected=('dispatching',),error_code=None)
            if operation['phase']=='rejected' and operation.get('error_code')=='cloud_mcp_refresh_in_progress':
                return await run_blocking(repo.wait_for_mcp_refresh,cid,sid,operation['command_id'])
            raise CloudError(operation.get('error_code') or 'cloud_queue_send_rejected') if operation['phase']=='rejected' else CloudError('cloud_queue_send_unconfirmed')
        except CloudError as error:
            logger.warning('cloud_queued_message_paused code=%s',error.code)
            if error.code=='cloud_queue_changed':return await run_blocking(domain.repo.operation,cid,sid)
            return await run_blocking(repo.update,cid,sid,'paused',error_code=error.code)

        except Exception as error:
            logger.exception('cloud_queued_message_failed error_type=%s',type(error).__name__)
            return await run_blocking(repo.update,cid,sid,'paused',error_code='cloud_queue_failed')

    async def cancel(self,domain,bid,sid,cid):
        with self.manager.operation(),domain.owner.operation():
            repo=QueueRepository(domain.repo.database);value=await run_blocking(repo.read,bid,sid)
            if value is None or value['command_id']!=cid:raise CloudError('cloud_queue_changed')
            return await run_blocking(repo.update,cid,sid,'cancelled',expected=('waiting','paused'))

    async def close(self):
        self.closing=True;jobs=tuple(self.jobs.values())
        for job in jobs:job.cancel()
        results=await await_settled(asyncio.gather(*jobs,return_exceptions=True))
        for error in results:
            if isinstance(error,BaseException) and not isinstance(error,asyncio.CancelledError):
                logger.error('cloud_queue_close_failed error_type=%s',type(error).__name__)
