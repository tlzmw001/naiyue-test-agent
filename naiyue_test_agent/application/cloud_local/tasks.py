"""Main-session delegation and owned producers; no conversation or memory writes."""
import asyncio
import logging
from contextlib import contextmanager,asynccontextmanager,nullcontext
from pathlib import Path

from naiyue_test_agent.agent_runtime.async_utils import await_settled,run_blocking
from naiyue_test_agent.cloud_files.cache import capture_cached
from naiyue_test_agent.cloud_files.client import CloudConnection
from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.cloud_files.profiles import ProfileBook,ProfileConnections
from naiyue_test_agent.contracts.cloud_inputs import Selection
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .preparation import prepare,checked_task,reconcile_created

logger=logging.getLogger(__name__)


class LocalTaskDomain:
    def __init__(self,owner,storage,project_id,authority=None,persistence=None):
        self.owner,self.repo,self.metadata=owner,storage.cloud_tasks,storage.sessions
        self.project_id=project_id;self.cache=storage.path.parent/'cloud-input-cache' if storage.path else None
        self.authority=authority
        self.persistence=persistence

    async def session(self,session_id):
        row=await run_blocking(self.metadata.get,session_id)
        if self.project_id is None or row['project_id']!=self.project_id:raise CloudError('cloud_project_required',409)
        return row

    async def capture(self,session_id,root,selection,limits,*,git=False,include=(),exclude=(),validate=None):
        with self.owner.operation():
            await self.session(session_id)
            if self.cache is None:raise CloudError('cloud_project_required')
            execution=await run_blocking(self.authority.resolve,self.project_id) if self.authority else None
            if execution is not None and execution.path!=Path(root):raise CloudError('cloud_source_identity_changed')
            def check_source():
                if execution is not None:
                    from naiyue_test_agent.execution_roots import validate_execution_root
                    validate_execution_root(execution,self.project_id)
                if validate is not None:validate()
            def freeze():
                check_source()
                plan=None
                if git:
                    from naiyue_test_agent.cloud_files.sources import select_git
                    plan=select_git(root,limits,include=include,exclude=exclude)
                selected=Selection.model_validate(plan.selection if plan else selection)
                if not git and selected.policy is not None:raise CloudError('cloud_source_plan_required',422)
                value=capture_cached(root,selected,self.cache,limits,source_receipt=plan.receipt if plan else None)
                check_source()
                return self.repo.add_source(session_id,self.project_id,value.model_dump(mode='json'))
            value=await run_blocking(freeze);captured=value['captured']
            return dict(source_handle=value['source_handle'],input_id=captured['input_id'],size=captured['size'])

    def own(self,task):
        self.owner._tasks.add(task);task.add_done_callback(self.owner.background_done)


class ManualCloudTasks:
    def __init__(self,settings,*,book=None,connections=None):
        self.settings=settings;self.book=book if book is not None else ProfileBook.read(settings.profiles_file)
        self.connections=connections if connections is not None else ProfileConnections(self.book)
        self.flights={};self.closing=False
        self.admission=asyncio.Lock()
        self.operations={}
        self.observers=set();self.downloads=set()
        self.slots=set()
        from .commands import ManualTaskCommands
        self.commands=ManualTaskCommands(self)

    @contextmanager
    def operation(self):
        if self.closing:raise CloudError('cloud_client_closing',503)
        done=asyncio.get_running_loop().create_future();self.operations[done]=asyncio.current_task()
        try:yield
        finally:self.operations.pop(done);done.set_result(None)

    def connection_settings(self,profile):return read_settings(profile.connection_file,CloudConnection)

    @asynccontextmanager
    async def connection(self,binding):
        client=await self.connections.get(binding['profile_id'],binding['instance_id']);primary=None
        try:yield client
        except BaseException as error:primary=error;raise
        finally:
            try:await self.connections.release(client)
            except BaseException as error:
                logger.error('cloud_connection_release_failed error_type=%s',type(error).__name__)
                if primary is None:raise

    async def submit(self,domain,project_name,request):
        with self.operation(),domain.owner.operation():
            async def admit():
                async with self.admission:return await self._submit(domain,project_name,request)
            return await await_settled(asyncio.create_task(admit()))

    async def _submit(self,domain,project_name,request):
        with domain.owner.operation():
            profile=self.book.require(request['profile_id'])
            if request['template_id'] not in profile.template_ids:raise CloudError('cloud_template_not_allowed',403)
            config=await run_blocking(self.connection_settings,profile)
            # Existing operations may still be read/retried at capacity.
            try:existing=await run_blocking(domain.repo.operation,request['command_id'],request['local_session_id'])
            except CloudError as error:
                if error.code!='cloud_operation_not_found':raise
                existing=None
            key=(domain.project_id,request['command_id'])
            slot=key not in self.flights and (existing is None or existing['phase'] not in ('prepared','cancelled'))
            if slot and len(self.flights)+len(self.slots)>=self.settings.max_preparations:
                raise CloudError('cloud_preparation_capacity',429)
            if slot:self.slots.add(key)
            try:
                parent=domain.persistence.history_lock(request['local_session_id']) if domain.persistence else nullcontext()
                async with parent:
                    result=await run_blocking(domain.repo.reserve,domain.project_id,request,
                        dict(instance_id=config.instance_id,project_name=project_name))
                self._launch(domain,result['operation'])
                return result
            finally:
                if slot:self.slots.remove(key)

    def _launch(self,domain,op):
        key=(domain.project_id,op['command_id'])
        if op['phase'] in ('prepared','cancelled') or key in self.flights:return
        if len(self.flights)+len(self.slots-{key})>=self.settings.max_preparations:raise CloudError('cloud_preparation_capacity',429)
        async def work():
            with domain.owner.operation():
                try:
                    binding=await run_blocking(domain.repo.binding,op['binding_id'],op['local_session_id'])
                    async with self.connection(binding) as client:
                        await run_blocking(prepare,domain,client,op['command_id'],op['local_session_id'])
                except CloudError as error:
                    logger.warning('cloud_manual_preparation_rejected code=%s',error.code)
                    if error.code=='cloud_preparation_cancelled':return
                    phase='unknown' if error.code in ('cloud_connection_failed','cloud_preparation_pending') else 'failed'
                    await run_blocking(domain.repo.fail,op['command_id'],op['local_session_id'],phase,error.code)
                except Exception as error:
                    logger.error('cloud_manual_preparation_failed error_type=%s',type(error).__name__)
                    await run_blocking(domain.repo.fail,op['command_id'],op['local_session_id'],'unknown','cloud_preparation_failed')
                finally:
                    await run_blocking(domain.repo.settle_cancel,op['command_id'],op['local_session_id'])
        task=asyncio.create_task(work());self.flights[key]=task;domain.own(task)
        def done(completed):
            if self.flights.get(key) is completed:del self.flights[key]
        task.add_done_callback(done)

    async def retry(self,domain,command_id,session_id):
        with self.operation(),domain.owner.operation():
            op=await run_blocking(domain.repo.operation,command_id,session_id)
            if op['kind']!='create':return self.commands.launch(domain,op,explicit=True)
            if op['cancel_requested']:
                await self._read(domain,op['binding_id'],session_id)
                return await run_blocking(domain.repo.operation,command_id,session_id)
            self._launch(domain,op)
            return op

    async def cancel(self,domain,command_id,session_id):
        with self.operation(),domain.owner.operation():
            op=await run_blocking(domain.repo.operation,command_id,session_id)
            if op['kind']!='create':raise CloudError('cloud_command_cancel_unavailable')
            result=await run_blocking(domain.repo.cancel,command_id,session_id,
                settled=(domain.project_id,command_id) not in self.flights)
            if (domain.project_id,command_id) not in self.flights:
                await run_blocking(domain.repo.settle_cancel,command_id,session_id)
                result['binding']=await run_blocking(domain.repo.binding,result['binding']['binding_id'],session_id)
            return result

    async def read(self,domain,binding_id,session_id):
        with self.operation():return await self._read(domain,binding_id,session_id)

    async def _read(self,domain,binding_id,session_id):
        with domain.owner.operation():
            binding=await run_blocking(domain.repo.binding,binding_id,session_id)
            if binding.get('deletion'):return binding
            if binding['task_id'] is None:
                op=await run_blocking(domain.repo.operation,binding['create_command_id'],session_id)
                if not op['may_have_task']:return binding
            async with self.connection(binding) as client:
                if binding['task_id'] is None:binding=await run_blocking(reconcile_created,domain,client,binding,session_id)
                task=checked_task(await run_blocking(client.request,'GET','/tasks/'+binding['task_id']),binding)
                if task.lifecycle=='deleted':
                    if task.delete_command_id is None:raise CloudError('cloud_delete_receipt_mismatch',502)
                    receipt=await run_blocking(client.request,'GET','/commands/'+task.delete_command_id)
                    return await run_blocking(domain.repo.commands.adopt_deleted,binding_id,session_id,task.model_dump(mode='json'),receipt)
                return await run_blocking(domain.repo.cache_task,binding_id,session_id,task.model_dump(mode='json'))

    async def drain(self):
        primary=None
        while self.flights or self.commands.flights:
            results=await await_settled(asyncio.gather(*tuple(self.flights.values()),
                *(v[1] for v in self.commands.flights.values()),return_exceptions=True))
            for result in results:
                if isinstance(result,BaseException) and primary is None:primary=result
        if primary is not None:raise primary

    async def view(self,domain,binding_id,session_id,view,params):
        if view not in ('history','snapshot','usage','models','history/threads','history/messages','results','result','result_changes','result_preview'):raise CloudError('cloud_view_not_found',404)
        with self.operation(),domain.owner.operation():
            binding=await run_blocking(domain.repo.binding,binding_id,session_id)
            if binding['task_id'] is None:raise CloudError('cloud_preparation_pending')
            if view=='history/messages':
                from urllib.parse import quote
                from pydantic import TypeAdapter
                from naiyue_test_agent.contracts.cloud_history import Identity
                params=dict(params);message=TypeAdapter(Identity).validate_python(params.pop('message_id'))
                view+='/'+quote(message,safe='')
            if view.startswith('result') and view!='results':
                from pydantic import TypeAdapter
                from naiyue_test_agent.contracts.cloud_tasks import UUIDText
                params=dict(params);rid=TypeAdapter(UUIDText).validate_python(params.pop('result_id'));tail=''
                if view=='result_changes':tail='/changes'
                if view=='result_preview':tail='/files/'+TypeAdapter(UUIDText).validate_python(params.pop('file_id'))+'/preview'
                view='results/'+rid+tail
            async with self.connection(binding) as client:
                return await run_blocking(client.request,'GET','/tasks/'+binding['task_id']+'/'+view,params=params)

    async def close(self):
        self.closing=True;primary=None
        observers=tuple(job for job in self.observers if job is not asyncio.current_task())
        for job in observers:job.cancel()
        if observers:
            results=await await_settled(asyncio.gather(*observers,return_exceptions=True))
            for error in results:
                if isinstance(error,BaseException) and not isinstance(error,asyncio.CancelledError):
                    logger.warning('cloud_observer_shutdown_failed error_type=%s',type(error).__name__)
        try:
            downloads=tuple(self.downloads)
            if downloads:
                results=await await_settled(asyncio.gather(*(lease.aclose() for lease in downloads),return_exceptions=True))
                for error in results:
                    if isinstance(error,BaseException):
                        logger.error('cloud_download_shutdown_failed error_type=%s',type(error).__name__)
                        if primary is None:primary=error
            if self.operations:await await_settled(asyncio.gather(*tuple(self.operations)))
            await self.drain()
        except BaseException as error:primary=error
        try:await self.connections.close()
        except BaseException as error:
            if primary is None:raise
            logger.error('cloud_manual_close_secondary_failed error_type=%s',type(error).__name__)
        if primary is not None:raise primary
