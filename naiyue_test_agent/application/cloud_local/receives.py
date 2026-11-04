"""Owned local downloads persist fixed metadata before streaming and publish no chat events."""
import asyncio,hashlib,logging,os,time
from uuid import uuid4
import httpx
from pydantic import ValidationError
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_receive import ReceiveRequest,ReceiveFile,MaterialRef
from naiyue_test_agent.contracts.cloud_results import ResultRecord,OutputManifest
from naiyue_test_agent.contracts.cloud_inputs import InputPackage
from naiyue_test_agent.cloud_files.archive import strict_json
from naiyue_test_agent.cloud_files.paths import check_links
from .receive_transfer import transfer

logger=logging.getLogger(__name__)


class LocalReceives:
    def __init__(self,manager):self.manager=manager;self.flights={};self.closing=False

    def _key(self,domain,rid):return domain.project_id,rid

    async def cleanup(self,domain,sid):
        from .receive_cleanup import reclaim
        with self.manager.operation(),domain.owner.operation():
            await domain.session(sid)
            async with self.manager.admission:
                if any(key[0]==domain.project_id for key in (*self.flights,*self.manager.deliveries.flights)):
                    raise CloudError('cloud_receive_busy',409)
                guard=domain.materials.files.use(cleanup=True);guard.__enter__()
            try:
                return await run_blocking(reclaim,domain.materials.files,domain.materials.repo,self.manager.settings.receive_limits)
            finally:guard.__exit__(None,None,None)

    async def read(self,domain,rid,sid):
        with self.manager.operation(),domain.owner.operation():
            domain.materials.limits=self.manager.settings.receive_limits
            record=await run_blocking(domain.materials.repo.read,rid,sid)
            if record.local_project_id!=domain.project_id:raise CloudError('cloud_material_identity_mismatch',403)
            return record.model_copy(update={'active':self._key(domain,rid) in self.flights})

    async def submit(self,domain,bid,result_id,value):
        request=ReceiveRequest.model_validate(value);manager=self.manager
        with manager.operation(),domain.owner.operation(),domain.materials.files.use():
            domain.materials.limits=manager.settings.receive_limits
            async with manager.admission:
                binding=await run_blocking(domain.repo.binding,bid,request.local_session_id)
                if self.closing:raise CloudError('cloud_client_closing',503)
                record=await run_blocking(domain.materials.repo.reserve,binding,result_id,request.model_dump(mode='json'),manager.settings.receive_limits)
                if record.phase=='reserved' and self._key(domain,record.receive_id) not in self.flights:
                    if len(self.flights)>=manager.settings.receive_limits.max_active:raise CloudError('cloud_receive_capacity',429)
                    self.launch(domain,record,request.artifact_ids)
                return record.model_copy(update={'active':self._key(domain,record.receive_id) in self.flights})

    async def retry(self,domain,rid,sid,*,refresh=False):
        with self.manager.operation(),domain.owner.operation(),domain.materials.files.use():
            domain.materials.limits=self.manager.settings.receive_limits
            async with self.manager.admission:
                record=await run_blocking(domain.materials.repo.read,rid,sid)
                if record.phase=='available' and not refresh or self._key(domain,rid) in self.flights:return record
                selection=await run_blocking(domain.materials.repo.request,rid,sid)
                if len(self.flights)>=self.manager.settings.receive_limits.max_active:raise CloudError('cloud_receive_capacity',429)
                if refresh:record=await run_blocking(domain.materials.repo.save,record.model_copy(update={'phase':'downloading','error_code':None}))
                self.launch(domain,record,selection['artifact_ids'],explicit_retry=True,refresh=refresh)
                return record.model_copy(update={'active':True})

    async def analysis(self,domain,rid,sid):
        with self.manager.operation(),domain.owner.operation():
            domain.materials.limits=self.manager.settings.receive_limits
            return await run_blocking(domain.materials.analysis,rid,sid)

    def launch(self,domain,record,selection,*,explicit_retry=False,refresh=False):
        key=self._key(domain,record.receive_id)
        if key in self.flights:return
        async def produce():
            with domain.owner.operation():
                try:
                    async with asyncio.timeout(self.manager.settings.receive_limits.transfer_timeout_seconds):
                        await self._receive(domain,record,selection,explicit_retry,refresh)
                except (CloudError,OSError,httpx.HTTPError,ValidationError,TimeoutError) as error:
                    code=error.code if isinstance(error,CloudError) else 'cloud_receive_failed'
                    logger.warning('cloud_receive_failed code=%s error_type=%s',code,type(error).__name__)
                    current=await run_blocking(domain.materials.repo.read,record.receive_id,record.local_session_id)
                    if current.phase!='available':
                        await run_blocking(domain.materials.repo.save,current.model_copy(update={'phase':'failed','error_code':code}))
        job=asyncio.create_task(produce());self.flights[key]=job;domain.own(job)
        def done(value):
            if self.flights.get(key) is value:self.flights.pop(key)
        job.add_done_callback(done)

    def _metadata(self,domain,client,record,selection):
        limits=self.manager.settings.receive_limits
        result=ResultRecord.model_validate(client.request('GET','/tasks/'+record.task_id+'/results/'+record.result_id))
        if (result.phase!='available' or result.bundle is None or result.boundary is None or result.plan.result_id!=record.result_id
                or result.plan.task_id!=record.task_id or result.plan.input_id!=record.input_id
                or result.plan.project_id!=domain.repo.binding(record.binding_id,record.local_session_id)['cloud_project_id']):
            raise CloudError('cloud_receive_identity_mismatch',502)
        wanted=set(selection);reports={f.artifact.artifact_id for f in result.bundle.files if f.role=='artifact'}
        if not wanted<=reports:raise CloudError('cloud_receive_selection_invalid',422)
        include_code=domain.materials.repo.request(record.receive_id,record.local_session_id).get('include_code',True)
        selected=[f for f in result.bundle.files if include_code and f.role in ('manifest','patch') or f.artifact.artifact_id in wanted]
        if len(selected)>limits.max_files or any(f.artifact.size>limits.max_file_bytes for f in selected):raise CloudError('cloud_material_budget',413)
        package=InputPackage.model_validate(client.request('GET','/inputs/'+record.input_id))
        if (package.input_id!=record.input_id or package.manifest.input_id!=record.input_id
                or package.manifest.selection['policy_sha256']!=hashlib.sha256(package.selection.canonical).hexdigest()):
            raise CloudError('cloud_receive_input_mismatch',502)
        source=domain.repo.source(record.source_handle,record.local_session_id)
        branch_available=bool(source['captured'].get('source_receipt'))
        files=[ReceiveFile(material_id=str(uuid4()),role=f.role,artifact=f.artifact) for f in selected]
        with domain.materials.files.publication:
            used=domain.materials.files.used_bytes(limits)
            additional=sum(f.artifact.size for f in selected if not os.path.lexists(domain.materials.files.blob(f.artifact.sha256)))
            return domain.materials.repo.assign(record.model_copy(update={'result':result,'input_package':package,'files':files,'branch_available':branch_available,'phase':'downloading'}),limits,used,additional)

    async def _receive(self,domain,record,selection,explicit_retry,refresh=False):
        identities=None
        if refresh and record.files:
            identities=await run_blocking(domain.materials.repo.identities,record.receive_id,record.local_session_id,domain.project_id)
            used=await run_blocking(domain.materials.files.used_bytes,self.manager.settings.receive_limits)
            if used+sum(f.artifact.size for f in record.files)>self.manager.settings.receive_limits.max_cache_bytes:raise CloudError('cloud_receive_capacity',429)
            await run_blocking(domain.materials.files.prepare_refresh,record)
            record=await run_blocking(domain.materials.repo.save,record.model_copy(update={'phase':'downloading','error_code':None,
                'files':[f.model_copy(update={'offset':0,'verified':False}) for f in record.files]}))
        if record.result is not None and record.input_package is not None and record.files and all(f.verified for f in record.files):
            record=await run_blocking(domain.materials.repo.save,record.model_copy(update={'phase':'verifying','error_code':None}))
            await run_blocking(self._publish,domain,record,identities)
            return
        deadline=time.monotonic()+self.manager.settings.receive_limits.transfer_timeout_seconds
        binding=await run_blocking(domain.repo.binding,record.binding_id,record.local_session_id)
        async with self.manager.connection(binding) as client:
            from .result_lease import renew,release
            await run_blocking(renew,client,record)
            try:
                if record.result is None:record=await run_blocking(self._metadata,domain,client,record,selection)
                record=await run_blocking(domain.materials.repo.save,record.model_copy(update={'phase':'downloading','error_code':None}))
                for i in range(len(record.files)):
                    record=await run_blocking(transfer,client,domain.materials.files,record,i,domain.materials.repo,self.manager.settings.receive_limits,deadline,explicit_retry)
            finally:
                try:await run_blocking(release,client,record)
                except (CloudError,OSError,httpx.HTTPError,ValidationError) as error:
                    logger.warning('cloud_result_lease_release_failed error_type=%s',type(error).__name__)
        record=await run_blocking(domain.materials.repo.save,record.model_copy(update={'phase':'verifying'}))
        await run_blocking(self._publish,domain,record,identities)

    def _publish(self,domain,record,identities=None):
        materials=[];limits=self.manager.settings.receive_limits
        manifest=next((f.artifact for f in record.files if f.role=='manifest'),None)
        if manifest is not None:
            self._manifest(domain,record,manifest,limits)
        elif domain.materials.repo.request(record.receive_id,record.local_session_id).get('include_code',True):
            raise CloudError('cloud_receive_manifest_mismatch',502)
        for item in record.files:
            a=item.artifact;actual=domain.materials.files.inspect(domain.materials.files.blob(a.sha256),a.size,a.sha256)
            ref=MaterialRef(material_id=item.material_id,receive_id=record.receive_id,task_id=record.task_id,result_id=record.result_id,
                input_id=record.input_id,title=a.relative_name,role=item.role,size=a.size,sha256=a.sha256,
                source_run_id=record.result.plan.source_run_id,outcome=record.result.boundary.outcome,captured_at=a.captured_at)
            materials.append((ref,actual))
        return domain.materials.repo.publish(record,materials,identities)

    def _manifest(self,domain,record,manifest,limits):
        raw=domain.materials.files.read(manifest,limits.max_file_bytes)
        output=OutputManifest.model_validate(strict_json(raw));package=record.input_package
        if (output.canonical!=raw or output.input_id!=record.input_id
                or output.policy_sha256!=package.manifest.selection['policy_sha256']):raise CloudError('cloud_receive_manifest_mismatch',502)
        check_links(output.entries)
    async def drain(self):
        while self.flights:await await_settled(asyncio.gather(*tuple(self.flights.values())))

    async def close(self):self.closing=True;await self.drain()
