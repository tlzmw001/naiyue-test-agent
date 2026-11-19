"""Explicit, owned delivery producers; GET never starts a filesystem operation."""
import asyncio
import logging
import os
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.cloud_delivery import DeliveryRequest,DeliveryRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_files.delivery_paths import directory_identity,component
from naiyue_test_agent.cloud_files.delivery_review import directory_scope

logger=logging.getLogger(__name__)


class LocalDeliveries:
    def __init__(self,manager):self.manager=manager;self.flights={};self.writers=set();self.destinations={}

    def _key(self,domain,cid):return domain.project_id,cid

    def root(self,domain,request):
        if request.destination_handle!=domain.project_id or domain.authority is None:raise CloudError('cloud_delivery_destination_forbidden',403)
        self._require_files(request.action)
        from naiyue_test_agent.application.projects.locations import canonical,directory_identity as observation
        root=canonical(request.destination_path);signature=directory_identity(root)
        if observation(root,signature)!=request.expected_directory:raise CloudError('cloud_delivery_root_changed')
        return root,signature

    @staticmethod
    def _require_files(action):
        if action!='files':raise CloudError('cloud_result_workflow_retired',410)

    async def prepare(self,domain,value,*,tool_plan=None):
        request=DeliveryRequest.model_validate(value);sid=request.local_session_id
        self._require_files(request.action)
        with self.manager.operation(),domain.owner.operation(),domain.materials.files.use():
            await domain.session(sid)
            root,signature=await run_blocking(self.root,domain,request)
            receive=await run_blocking(domain.materials.repo.read,request.receive_id,sid)
            if receive.phase!='available' or receive.local_project_id!=domain.project_id:raise CloudError('cloud_delivery_receive_unavailable')
            if request.name:component(request.name)
            record=DeliveryRecord(command_id=request.command_id,local_project_id=domain.project_id,local_session_id=sid,
                receive_id=request.receive_id,action=request.action,name=request.name,phase='preparing',destination_path=str(root/request.name) if request.action=='files' else None)
            async with self.manager.admission:
                key=self._key(domain,record.command_id)
                if key not in self.flights and len(self.flights)>=self.manager.settings.receive_limits.max_active:raise CloudError('cloud_delivery_capacity',429)
                record=await run_blocking(domain.deliveries.reserve,request,record,dict(root_identity=signature),self.manager.settings.receive_limits.max_results)
                _,stored=await run_blocking(domain.deliveries.read,record.command_id,sid)
                if stored.get('tool_download')!=tool_plan:raise CloudError('cloud_delivery_request_conflict')
                if record.phase in ('prepared','completed','executing','partial') or key in self.flights:return record
                if record.phase=='unknown':
                    _,metadata=await run_blocking(domain.deliveries.read,record.command_id,sid)
                    if metadata.get('verified'):return record
                record=await run_blocking(domain.deliveries.save,record.model_copy(update={'phase':'preparing','error_code':None}))
                self.launch(domain,record,execute=False)
                return record

    async def read(self,domain,cid,sid):
        with self.manager.operation(),domain.owner.operation():
            record,meta=await run_blocking(domain.deliveries.read,cid,sid)
            if record.local_project_id!=domain.project_id:raise CloudError('cloud_project_mismatch',403)
            if record.action!='files':
                if record.phase in ('executing','partial','unknown'):
                    return record.model_copy(update={'error_code':'legacy_recovery_required'})
                if record.phase in ('preparing','prepared'):
                    return record.model_copy(update={'error_code':'cloud_result_workflow_retired'})
                return record
            if ('tool_download' not in meta and record.phase in ('preparing','executing')
                    and self._key(domain,cid) not in self.flights):
                record=await run_blocking(domain.deliveries.save,record.model_copy(update={'phase':'unknown','error_code':'cloud_delivery_interrupted'}))
            return record

    async def recheck(self,domain,cid,sid):
        with self.manager.operation(),domain.owner.operation(),domain.materials.files.use():
            async with self.manager.admission:
                record,meta=await run_blocking(domain.deliveries.read,cid,sid)
                self._require_files(record.action)
                if 'tool_download' in meta:raise CloudError('cloud_delivery_tool_owned')
                if record.phase=='completed' or self._key(domain,cid) in self.flights:return record
                work=await run_blocking(domain.materials.files.directory,'deliveries',cid)
                if (work/'apply/operation.json').exists() or (work/'export/export.json').exists():raise CloudError('cloud_delivery_interrupted')
                if len(self.flights)>=self.manager.settings.receive_limits.max_active:raise CloudError('cloud_delivery_capacity',429)
                meta.pop('verified',None);meta.pop('force',None)
                record=await run_blocking(domain.deliveries.save,record.model_copy(update={'phase':'preparing','error_code':None,'force':False}),meta)
                self.launch(domain,record,execute=False);return record

    async def execute(self,domain,cid,sid,*,force=False,tool_plan=None):
        with self.manager.operation(),domain.owner.operation(),domain.materials.files.use():
            async with self.manager.admission:
                record,meta=await run_blocking(domain.deliveries.read,cid,sid)
                key=self._key(domain,cid)
                self._require_files(record.action)
                if meta.get('tool_download')!=tool_plan:raise CloudError('cloud_delivery_request_conflict')
                if record.local_project_id!=domain.project_id:raise CloudError('cloud_project_mismatch',403)
                if record.phase=='completed' or key in self.flights:return record
                if len(self.flights)>=self.manager.settings.receive_limits.max_active:raise CloudError('cloud_delivery_capacity',429)
                if not meta.get('verified') or record.conflicts and not (force and record.force_allowed):raise CloudError('cloud_delivery_not_prepared')
                if domain.project_id in self.writers:raise CloudError('cloud_delivery_project_busy')
                root=await run_blocking(self._check_root,domain,meta)
                scope=await run_blocking(directory_scope,root)
                legacy=await run_blocking(domain.deliveries.legacy_recovery_roots,domain.project_id)
                if legacy:
                    execution=await run_blocking(domain.authority.resolve,domain.project_id)
                    execution_scope=await run_blocking(directory_scope,execution.path)
                    for old,known in legacy:
                        if known!=execution_scope[0]:
                            raise CloudError('legacy_recovery_required')
                        if old.action=='export':
                            target=execution.path/component(old.name)
                            # Account for case-insensitive paths, even when the original
                            # export stopped before publishing its directory.
                            parents=(root,*root.parents)
                            if (str(target).casefold() in {str(p).casefold() for p in parents}
                                    or str(root).casefold() in {str(p).casefold() for p in target.parents}):
                                raise CloudError('legacy_recovery_required')
                            if os.path.lexists(target):
                                signature=await run_blocking(directory_identity,target)
                                if signature in scope:
                                    raise CloudError('legacy_recovery_required')
                        elif scope[0] in execution_scope or execution_scope[0] in scope:
                            raise CloudError('legacy_recovery_required')
                if any(scope[0] in other or other[0] in scope for other in self.destinations.values()):
                    raise CloudError('cloud_delivery_destination_busy')
                if 'force' in meta and meta['force']!=force:raise CloudError('cloud_delivery_request_conflict')
                meta['force']=force
                record=await run_blocking(domain.deliveries.save,record.model_copy(update={'phase':'executing','error_code':None,'force':force}),meta)
                self.destinations[key]=scope
                self.writers.add(domain.project_id);self.launch(domain,record,execute=True)
                return record

    def _check_root(self,domain,meta):
        root,signature=self.root(domain,DeliveryRequest.model_validate(meta['request']))
        if signature!=meta['root_identity']:raise CloudError('cloud_delivery_root_changed')
        return root

    def launch(self,domain,record,*,execute):
        key=self._key(domain,record.command_id)
        async def produce():
            with domain.owner.operation():
                try:
                    receive=await run_blocking(domain.materials.repo.read,record.receive_id,record.local_session_id)
                    work=await run_blocking(domain.materials.files.directory,'deliveries',record.command_id)
                    self._require_files(record.action)
                    from .delivery_files import deliver_files
                    await run_blocking(deliver_files,self,domain,record,receive,work,execute)
                except Exception as error:
                    code=error.code if isinstance(error,CloudError) else 'cloud_delivery_failed'
                    logger.warning('cloud_delivery_failed code=%s error_type=%s',code,type(error).__name__)
                    current,meta=await run_blocking(domain.deliveries.read,record.command_id,record.local_session_id)
                    if current.phase!='completed':
                        await run_blocking(domain.deliveries.save,current.model_copy(update={
                            'phase':'partial' if execute else 'failed','error_code':code}))
                finally:
                    if execute:
                        self.writers.discard(domain.project_id);self.destinations.pop(key,None)
        task=asyncio.create_task(produce());self.flights[key]=task;domain.own(task)
        task.add_done_callback(lambda job:self.flights.pop(key,None))

    async def close(self):
        if self.flights:await await_settled(asyncio.gather(*tuple(self.flights.values())))
