"""Explicit, owned delivery producers; GET never starts a filesystem operation."""
import asyncio
import logging
import os
from pathlib import Path
import tempfile
from pydantic import ValidationError
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.cloud_delivery import DeliveryRequest,DeliveryRecord,DeliveryChange
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_results import OutputManifest
from naiyue_test_agent.execution_roots import validate_execution_root
from naiyue_test_agent.cloud_files.delivery_paths import directory_identity,component
from naiyue_test_agent.cloud_files.delivery_apply import preflight,apply_changes,export_tree
from naiyue_test_agent.cloud_files.delivery_review import review,approved,rebased,directory_scope
from naiyue_test_agent.contracts.cloud_events import digest
from .delivery_inputs import source_for,original_archive,recover_archive

logger=logging.getLogger(__name__)


class LocalDeliveries:
    def __init__(self,manager):self.manager=manager;self.flights={};self.writers=set();self.destinations={}

    def _key(self,domain,cid):return domain.project_id,cid

    def root(self,domain,request):
        if request.destination_handle!=domain.project_id or domain.authority is None:raise CloudError('cloud_delivery_destination_forbidden',403)
        if request.action=='files':
            from naiyue_test_agent.application.projects.locations import canonical,directory_identity as observation
            root=canonical(request.destination_path);signature=directory_identity(root)
            if observation(root,signature)!=request.expected_directory:raise CloudError('cloud_delivery_root_changed')
            return root,signature
        execution=domain.authority.resolve(domain.project_id);validate_execution_root(execution,domain.project_id)
        return execution.path,directory_identity(execution.path)

    async def prepare(self,domain,value):
        request=DeliveryRequest.model_validate(value);sid=request.local_session_id
        with self.manager.operation(),domain.owner.operation(),domain.materials.files.use():
            await domain.session(sid)
            root,signature=await run_blocking(self.root,domain,request)
            receive=await run_blocking(domain.materials.repo.read,request.receive_id,sid)
            if receive.phase!='available' or receive.local_project_id!=domain.project_id:raise CloudError('cloud_delivery_receive_unavailable')
            if request.action in ('export','files') and request.name:component(request.name)
            record=DeliveryRecord(command_id=request.command_id,local_project_id=domain.project_id,local_session_id=sid,
                receive_id=request.receive_id,action=request.action,name=request.name,phase='preparing',destination_path=str(root/request.name) if request.action=='files' else None)
            async with self.manager.admission:
                key=self._key(domain,record.command_id)
                if key not in self.flights and len(self.flights)>=self.manager.settings.receive_limits.max_active:raise CloudError('cloud_delivery_capacity',429)
                record=await run_blocking(domain.deliveries.reserve,request,record,dict(root_identity=signature),self.manager.settings.receive_limits.max_results)
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
            if record.phase in ('preparing','executing') and self._key(domain,cid) not in self.flights:
                record=await run_blocking(domain.deliveries.save,record.model_copy(update={'phase':'unknown','error_code':'cloud_delivery_interrupted'}))
            return record

    async def recheck(self,domain,cid,sid):
        with self.manager.operation(),domain.owner.operation(),domain.materials.files.use():
            async with self.manager.admission:
                record,meta=await run_blocking(domain.deliveries.read,cid,sid)
                if record.phase=='completed' or self._key(domain,cid) in self.flights:return record
                work=await run_blocking(domain.materials.files.directory,'deliveries',cid)
                if (work/'apply/operation.json').exists() or (work/'export/export.json').exists():raise CloudError('cloud_delivery_interrupted')
                if len(self.flights)>=self.manager.settings.receive_limits.max_active:raise CloudError('cloud_delivery_capacity',429)
                meta.pop('verified',None);meta.pop('force',None)
                record=await run_blocking(domain.deliveries.save,record.model_copy(update={'phase':'preparing','error_code':None,'force':False}),meta)
                self.launch(domain,record,execute=False);return record

    async def execute(self,domain,cid,sid,*,force=False):
        with self.manager.operation(),domain.owner.operation(),domain.materials.files.use():
            async with self.manager.admission:
                record,meta=await run_blocking(domain.deliveries.read,cid,sid)
                key=self._key(domain,cid)
                if record.local_project_id!=domain.project_id:raise CloudError('cloud_project_mismatch',403)
                if record.phase=='completed' or key in self.flights:return record
                if len(self.flights)>=self.manager.settings.receive_limits.max_active:raise CloudError('cloud_delivery_capacity',429)
                if not meta.get('verified') or record.conflicts and not (force and record.force_allowed):raise CloudError('cloud_delivery_not_prepared')
                if domain.project_id in self.writers:raise CloudError('cloud_delivery_project_busy')
                root=await run_blocking(self._check_root,domain,meta)
                scope=await run_blocking(directory_scope,root)
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
                    if record.action=='files':
                        from .delivery_files import deliver_files
                        await run_blocking(deliver_files,self,domain,record,receive,work,execute)
                        return
                    binding,source=await run_blocking(source_for,domain,receive)
                    archive=await run_blocking(original_archive,source,receive.input_package)
                    work=await run_blocking(domain.materials.files.directory,'deliveries',record.command_id)
                    if archive is None:
                        archive=work/'archive'
                        if not archive.exists():
                            used=await run_blocking(domain.materials.files.used_bytes,self.manager.settings.receive_limits)
                            if used+receive.input_package.size>self.manager.settings.receive_limits.max_cache_bytes:
                                raise CloudError('cloud_delivery_capacity',429)
                            async with self.manager.connection(binding) as client:
                                await run_blocking(recover_archive,client,receive.input_package,archive,self.manager.settings.input_limits)
                    await run_blocking(self._run,domain,record,receive,source,archive,work,execute)
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

    def _run(self,domain,record,receive,source,archive,work,execute):
        from naiyue_test_agent.cloud_files.delivery_verify import verify_delivery
        _,meta=domain.deliveries.read(record.command_id,record.local_session_id)
        root=self._check_root(domain,meta);settings=self.manager.settings
        manifest=next(f for f in receive.files if f.role=='manifest');patch=next(f for f in receive.files if f.role=='patch')
        for file in (manifest,patch):domain.materials._get(record.local_session_id,file.material_id)
        manifest_raw=domain.materials.files.read(manifest.artifact,settings.result_limits.max_metadata_bytes)
        output=OutputManifest.model_validate_json(manifest_raw)
        from naiyue_test_agent.cloud_files.delivery_paths import portable_conflicts
        invalid=portable_conflicts(e.path for e in output.entries)
        if invalid:
            if execute:raise CloudError('cloud_delivery_path_unsupported',422)
            meta['verified']=False
            domain.deliveries.save(record.model_copy(update={'phase':'prepared','conflicts':invalid,'force_allowed':False,
                'error_code':'cloud_delivery_path_unsupported'}),meta)
            return
        required=receive.input_package.size+sum(e.size for e in receive.input_package.manifest.entries)*2+sum(e.size for e in output.entries)*3
        if domain.materials.files.used_bytes(settings.receive_limits)+required>settings.receive_limits.max_cache_bytes:
            raise CloudError('cloud_delivery_capacity',429)
        # Verified trees may contain safe symlinks. Keep them out of the blob
        # cache, whose scanner deliberately accepts only private regular files.
        with tempfile.TemporaryDirectory(prefix='nyt-delivery-') as temporary:
            verified=verify_delivery(receive.input_package,archive,manifest_raw,domain.materials.files.blob(patch.artifact.sha256),
                Path(temporary).resolve(),settings.input_limits,settings.result_limits)
            if record.action=='apply':
                key=digest(dict(root=str(root),identity=meta['root_identity'],instance=receive.instance_id,task=receive.task_id,input=receive.input_id))
                if execute:
                    if domain.deliveries.baseline(key)[0]!=meta.get('base_command_id'):raise CloudError('cloud_delivery_baseline_changed')
                    entries=meta['base_entries']
                else:
                    previous,base=domain.deliveries.baseline(key)
                    entries=base.get('output_entries',[e.model_dump() for e in verified.input_manifest.entries])
                    meta.update(baseline_key=key,base_command_id=previous,base_entries=entries,
                        result_id=receive.result_id,output_entries=[e.model_dump() for e in verified.output_manifest.entries])
                    record=record.model_copy(update={'base_result_id':base.get('result_id')})
                verified=rebased(verified,entries)
            changes=[DeliveryChange(path=c['path'],operation='add' if c['old'] is None else 'delete' if c['new'] is None else 'modify') for c in verified.changes]
            if not execute:
                conflicts=[]
                if record.action=='apply':
                    meta['observed'],conflicts=review(root,verified,settings.receive_limits.max_file_bytes)
                    if sum(e['size'] for e in meta['observed'].values() if e)+required+domain.materials.files.used_bytes(settings.receive_limits)>settings.receive_limits.max_cache_bytes:
                        raise CloudError('cloud_delivery_capacity',413)
                elif record.action=='export':
                    if os.path.lexists(root/record.name):conflicts=[record.name]
                else:
                    from naiyue_test_agent.cloud_files.delivery_branch import branch_preflight
                    receipt=source['captured'].get('source_receipt')
                    if not receipt:raise CloudError('cloud_delivery_branch_source_required')
                    conflicts=branch_preflight(verified,Path(receipt),root,record.name,settings.result_limits)
                meta['verified']=True
                domain.deliveries.save(record.model_copy(update={'phase':'prepared','changes':changes,'conflicts':conflicts,'force_allowed':record.action=='apply' and bool(conflicts)}),meta)
                return
            result={}
            if record.action=='apply':
                def checkpoint(progress):
                    meta['done']=list(progress['done']);domain.deliveries.save(record.model_copy(update={'done':meta['done']}),meta)
                verified=approved(root,verified,meta['observed'],meta.get('force',False),continuing=(work/'apply/operation.json').exists())
                result=apply_changes(root,verified,work/'apply',checkpoint)
            elif record.action=='export':result=export_tree(root,record.name,verified,work/'export')
            else:
                from naiyue_test_agent.cloud_files.delivery_branch import branch_delivery
                receipt=source['captured'].get('source_receipt')
                if not receipt:raise CloudError('cloud_delivery_branch_source_required')
                result=branch_delivery(verified,Path(receipt),root,record.name,settings.result_limits,journal=work/'branch.json')
            domain.deliveries.complete(record.model_copy(update={'phase':'completed','changes':changes,'error_code':None,
                'done':result.get('done',[]),'branch_head':result.get('branch_head')}),meta)

    async def close(self):
        if self.flights:await await_settled(asyncio.gather(*tuple(self.flights.values())))
