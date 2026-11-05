"""Short local dispatch ownership; remote runs outlive the local service."""
import asyncio
import logging
from naiyue_test_agent.agent_runtime.async_utils import await_settled,run_blocking
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_local_tasks import command_summary
from . import command_dispatch

logger=logging.getLogger(__name__)


class ManualTaskCommands:
    def __init__(self,manager):self.manager=manager;self.flights={};self.slots=set()

    def _capacity(self,kind,key):
        control=kind in ('stop','recover','delete')
        used=sum((v[0] in ('stop','recover','delete'))==control for k,v in self.flights.items() if k!=key)
        used+=sum((k[2] in ('stop','recover','delete'))==control for k in self.slots if k[:2]!=key)
        maximum=self.manager.settings.max_controls if control else self.manager.settings.max_commands
        if used>=maximum:raise CloudError('cloud_command_capacity',429)

    async def submit(self,domain,bid,kind,request):
        manager=self.manager
        with manager.operation(),domain.owner.operation():
            async def admit():
                async with manager.admission:
                    key=(domain.project_id,request['command_id']);slot=(*key,kind)
                    try:previous=await run_blocking(domain.repo.operation,request['command_id'],request['local_session_id'])
                    except CloudError as error:
                        if error.code!='cloud_operation_not_found':raise
                        previous=None
                    if previous is None:self._capacity(kind,key)
                    self.slots.add(slot)
                    try:
                        binding=await run_blocking(domain.repo.binding,bid,request['local_session_id'])
                        if (domain.project_id,binding['create_command_id']) in manager.flights:raise CloudError('cloud_preparation_pending')
                        op=await run_blocking(domain.repo.commands.reserve,bid,request['local_session_id'],kind,request)
                        if previous is None:self.launch(domain,op)
                        return op
                    finally:self.slots.remove(slot)
            return await await_settled(asyncio.create_task(admit()))

    def launch(self,domain,op,*,explicit=False):
        if op.get('error_code')=='cloud_task_deleted':return op
        key=(domain.project_id,op['command_id'])
        receipt=op['steps']['primary'].get('receipt')
        if receipt and receipt['status'] in ('succeeded','failed'):return op
        if key in self.flights:return op
        self._capacity(op['kind'],key)
        async def work():
            with domain.owner.operation():
                try:
                    binding=await run_blocking(domain.repo.binding,op['binding_id'],op['local_session_id'])
                    async with self.manager.connection(binding) as client:
                        await run_blocking(command_dispatch.execute,domain,client,op,self.manager.settings,explicit=explicit)
                except Exception as error:
                    code=error.code if isinstance(error,CloudError) else 'cloud_command_failed'
                    logger.warning('cloud_manual_command_failed kind=%s code=%s error_type=%s',op['kind'],code,type(error).__name__)
                    current=await run_blocking(domain.repo.operation,op['command_id'],op['local_session_id'])
                    pending=any(i.get('sent') and not i.get('rejected') and i.get('receipt',{}).get('status') not in ('succeeded','failed')
                        for i in current['steps'].values())
                    phase='waiting_runner' if code=='cloud_runner_pending' else 'unknown' if pending else 'rejected'
                    await run_blocking(domain.repo.commands.fail,op['command_id'],op['local_session_id'],phase,code)
        task=asyncio.create_task(work());self.flights[key]=(op['kind'],task);domain.own(task)
        def done(value):
            if self.flights.get(key,(None,None))[1] is value:self.flights.pop(key)
        task.add_done_callback(done)
        return op

    async def reconcile(self,domain,cid,sid):
        with self.manager.operation(),domain.owner.operation():
            op=await run_blocking(domain.repo.operation,cid,sid)
            return await self._observe(domain,op)

    async def _observe(self,domain,op):
        if op['kind'] in ('create','queued_message') or op['phase'] in ('succeeded','failed','rejected','cancelled'):return op
        binding=await run_blocking(domain.repo.binding,op['binding_id'],op['local_session_id'])
        async with self.manager.connection(binding) as client:
            return await run_blocking(command_dispatch.observe,domain,client,op)

    async def list(self,domain,bid,sid,*,offset=0,limit=50):
        with self.manager.operation(),domain.owner.operation():
            values=await run_blocking(domain.repo.commands.index,bid,sid,offset=offset,limit=limit)
            result=[];disconnected=False
            for cid in values:
                value=await run_blocking(domain.repo.operation,cid,sid)
                try:
                    if not disconnected:value=await self._observe(domain,value)
                except CloudError as error:
                    if error.code!='cloud_connection_failed':raise
                    disconnected=True
                    logger.warning('cloud_command_observation_failed code=%s',error.code)
                    # A transport failure cannot erase a previously verified receipt.
                    value=await run_blocking(domain.repo.operation,cid,sid)
                result.append(command_summary(value))
            return result

    async def delete_preview(self,domain,bid,sid):
        with self.manager.operation(),domain.owner.operation():
            binding=await run_blocking(domain.repo.binding,bid,sid)
            if not binding['retained'] or binding['task_id'] is None:raise CloudError('cloud_task_unavailable')
            async with self.manager.connection(binding) as client:
                value=await run_blocking(client.request,'GET','/tasks/'+binding['task_id']+'/delete-preview')
                if value.get('instance_id')!=binding['instance_id'] or value.get('task_id')!=binding['task_id']:
                    raise CloudError('cloud_task_identity_mismatch',502)
                return value
