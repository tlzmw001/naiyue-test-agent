"""Owner-loop domain API; Docker work is drained outside the event loop."""
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.execution.docker_files import DockerFileSystem
from .service import EnvironmentService
from .operations import SandboxOperationService
from .inputs import InputService
from .results import ResultService
from .delivery import DeliveryService


class EnvironmentAPI:
    def __init__(self,owner,hosts):
        self._owner,self._hosts=owner,hosts
        self._service=EnvironmentService(hosts.projects,hosts.settings.sandbox)
        self._operations=SandboxOperationService(self._service)
        self._service.operations=self._operations
        self._inputs=InputService(self._service,self._operations)
        self._results=ResultService(self._service,self._operations,self._inputs)
        self._delivery=DeliveryService(self._results,hosts.settings.delivery)
        owner.own_environments(self._operations)

    async def delivery_options(self,binding_id,result_id):
        with self._owner.operation():return await run_blocking(self._delivery.options,binding_id,result_id)

    async def delivery_operations(self,binding_id,result_id):
        with self._owner.operation():return await run_blocking(self._delivery.list,binding_id,result_id)

    async def deliver(self,binding_id,result_id,command):
        with self._owner.operation(),self._operations.accepting():
            row=await self.get(binding_id)
            async with self._hosts.reserve_delivery(row.project_id):
                return await run_blocking(self._delivery.execute,binding_id,result_id,command)

    async def acknowledge_delivery(self,binding_id,result_id,operation_id):
        with self._owner.operation():
            row=await self.get(binding_id)
            async with self._hosts.reserve_delivery(row.project_id):
                return await run_blocking(self._delivery.acknowledge,binding_id,result_id,operation_id)

    async def result_options(self,binding_id):
        with self._owner.operation():return await run_blocking(self._results.options,binding_id)

    async def capture_result(self,binding_id,*,result_id,include_untracked):
        with self._owner.operation():return await run_blocking(self._results.capture,binding_id,result_id,include_untracked)

    async def results(self,binding_id):
        with self._owner.operation():return await run_blocking(self._results.list,binding_id)

    async def result(self,binding_id,result_id):
        with self._owner.operation():return await run_blocking(self._results.get,binding_id,result_id)

    async def result_diff(self,binding_id,result_id,path):
        with self._owner.operation():return await run_blocking(self._results.diff,binding_id,result_id,path)

    async def prepare_input(self,project_id,*,input_id,include_untracked=()):
        with self._owner.operation():
            return await run_blocking(self._inputs.prepare,project_id,input_id=input_id,
                include_untracked=include_untracked)

    async def input(self,project_id,input_id):
        with self._owner.operation():return await run_blocking(self._inputs.get,project_id,input_id)

    async def options(self,project_id):
        with self._owner.operation():return await run_blocking(self._inputs.options,project_id)

    async def install_input(self,binding_id,input_id):
        with self._owner.operation():return await run_blocking(self._inputs.install,binding_id,input_id)

    async def create(self,project_id,session_id):
        with self._owner.operation():
            rows=await self._hosts.list_sessions(project_id)
            if not any(row['session_id']==session_id for row in rows):raise SandboxError('env_session_mismatch')
            return await run_blocking(self._service.create,project_id,session_id)

    async def list(self,project_id):
        with self._owner.operation():return await run_blocking(self._service.list,project_id)

    async def get(self,binding_id):
        with self._owner.operation():return await run_blocking(self._service.get,binding_id)

    async def _action(self,binding_id,action):
        with self._owner.operation():return await run_blocking(self._service.action,binding_id,action)

    async def start(self,binding_id):return await self._action(binding_id,'start')
    async def stop(self,binding_id):return await self._action(binding_id,'stop')
    async def destroy(self,binding_id):return await self._action(binding_id,'destroy')
    async def recover(self,binding_id):return await self._action(binding_id,'recover')

    async def files(self,binding_id,*,project_id,session_id):
        with self._owner.operation():
            row=await run_blocking(self._service.validate,binding_id)
            if row.project_id!=project_id or row.session_id!=session_id:raise SandboxError('env_session_mismatch')
            return DockerFileSystem(self._service,row)

    async def processes(self,binding_id,*,project_id,session_id):
        from naiyue_test_agent.execution.docker_processes import DockerProcesses
        with self._owner.operation():
            row=await run_blocking(self._service.get,binding_id)
            if row.project_id!=project_id or row.session_id!=session_id:raise SandboxError('env_session_mismatch')
            await run_blocking(self._service.validate,binding_id)
            return DockerProcesses(self._operations,row,self._owner)

    async def tasks(self,binding_id,*,project_id,session_id):
        from .tasks import SandboxTasks
        with self._owner.operation():
            row=await run_blocking(self._service.get,binding_id)
            if row.project_id!=project_id or row.session_id!=session_id:raise SandboxError('env_session_mismatch')
            records=await run_blocking(lambda:self._operations.records().list(binding_id))
            for item in records:
                if item['kind'] in ('shell','argv') and item['state'] not in ('completed','failed','stopped'):
                    self._operations.watch(binding_id,item['operation_id'],row.settings['poll_milliseconds'])
            return SandboxTasks(self._operations,row,self._owner)

    async def operation_status(self,binding_id,operation_id):
        with self._owner.operation():
            row=await run_blocking(self._operations.refresh,binding_id,operation_id)
            if row['kind']=='git_input' and row['state']=='completed':
                await run_blocking(self._inputs.confirm,binding_id,row)
            return row
