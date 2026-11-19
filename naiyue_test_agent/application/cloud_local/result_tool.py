"""Session-scoped projection and continuation of existing receive/delivery journals."""
import asyncio
import logging
from pathlib import Path

from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.application.projects.locations import directory_identity, directory_facts
from naiyue_test_agent.contracts.cloud_delivery import DeliveryRecord, DeliveryRequest
from naiyue_test_agent.contracts.cloud_results import ResultRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.cloud_files.delivery_review import observed_entry

logger = logging.getLogger(__name__)
RETRYABLE = frozenset({
    'cloud_connection_failed', 'cloud_delivery_project_busy', 'cloud_delivery_destination_busy',
    'cloud_receive_busy', 'cloud_download_interrupted', 'cloud_receive_failed',
})



class LocalResultTool:
    def __init__(self, manager, domain):
        self.manager = manager
        self.domain = domain

    async def query(self, sid, request):
        domain = self.domain
        offset, limit = request['offset'], request['limit']
        bid, rid = request.get('binding_id'), request.get('result_id')
        with self.manager.operation(), domain.owner.operation():
            await domain.session(sid)
            if bid is None:
                level = 'tasks'
                rows = await run_blocking(domain.repo.list, sid, offset=offset, limit=limit)
                items = [dict(binding_id=r['binding_id'], task_id=r['task_id'],
                              title=r.get('cloud', {}).get('title') if r.get('cloud') else None,
                              state=(r.get('cloud') or {}).get('lifecycle',r['preparation'])) for r in rows]
            elif rid is None:
                level = 'results'
                rows = await self.manager.view(domain,bid,sid,'results',dict(offset=offset,limit=limit))
                items = [dict(result_id=r['result_id'], source_run_id=r['source_run_id'],
                              phase=r['phase']) for r in rows]
            else:
                level = 'artifacts'
                result = await self.result(sid,bid,rid)
                rows = [slot.artifact for slot in result.bundle.files if slot.role=='artifact'][offset:offset+limit]
                items = [a.model_dump(include={'artifact_id','relative_name','size','sha256','media_type'}) for a in rows]
        return dict(action='query',level=level,items=items,next_offset=offset+limit if len(items)==limit else None)

    async def result(self,sid,bid,rid):
        value = await self.manager.view(self.domain,bid,sid,'result',dict(result_id=rid))
        record = ResultRecord.model_validate(value)
        if record.phase!='available' or record.bundle is None:
            raise CloudError('result_not_available',404)
        return record

    def _cached(self,sid,bid,rid,aid):
        domain=self.domain
        binding=domain.repo.binding(bid,sid)
        for receive_id,file in domain.materials.repo.available_artifacts(binding,rid,aid):
            try:
                ref=domain.materials._get(sid,file.material_id)
            except CloudError as error:
                if error.code not in ('cloud_material_not_found','cloud_material_unavailable'):raise
                continue
            if (ref.receive_id,ref.result_id,ref.task_id,ref.role,ref.size,ref.sha256)==(
                    receive_id,rid,binding['task_id'],'artifact',file.artifact.size,file.artifact.sha256):
                return receive_id,file.artifact
        return None

    def plan(self,sid,cid):
        record,meta = self.domain.deliveries.read(cid,sid)
        plan = meta.get('tool_download')
        if record.local_project_id!=self.domain.project_id or not plan:
            raise CloudError('cloud_download_not_found',404)
        return plan

    async def status(self,sid,cid):
        await self.domain.session(sid)
        plan = await run_blocking(self.plan,sid,cid)
        record,meta = await run_blocking(self.domain.deliveries.read,cid,sid)
        key = (self.domain.project_id,cid)
        phase = record.phase
        if key in self.manager.result_tool_flights:
            state = 'in_progress'
        elif phase=='prepared' and record.conflicts and not plan['force']:
            state = 'conflict'
        elif phase in ('completed','failed','partial'):
            state = phase
        else:
            state = 'unknown'
        result = dict(action='status',operation_id=cid,state=state,**plan)
        try:
            receive=await self.manager.receives.read(self.domain,record.receive_id,sid)
        except CloudError as error:
            if error.code!='cloud_receive_not_found':raise
            receive=None
        received=next((file.offset for file in receive.files if file.artifact.artifact_id==plan['artifact_id']),0) if receive else 0
        result['progress']=dict(received_bytes=received,total_bytes=plan['size'],
            saved_bytes=plan['size'] if state=='completed' or Path(plan['path']).name in record.done else 0)
        step='write' if meta.get('verified') else 'preflight' if receive and receive.phase=='available' else 'receive'
        if state=='unknown':
            result['error']=dict(code='cloud_download_interrupted',step=step,
                reason='Resume the original download with its operation ID.',retryable=True)
        if record.error_code:
            result['error'] = dict(code=record.error_code,step=step,reason=record.error_code.replace('_',' '),
                                   retryable=record.error_code in RETRYABLE or phase=='partial')
        if state=='conflict':
            result['conflict'] = dict(path=plan['path'],type='different_content',force_allowed=record.force_allowed)
        if state=='completed':
            path=Path(plan['path'])
            try:
                root=await run_blocking(self.manager.deliveries._check_root,self.domain,meta)
                entry=await run_blocking(lambda: observed_entry(root,path.name,plan['size'],ordinary=True))
                available=entry is not None and (entry.sha256,entry.size)==(plan['sha256'],plan['size'])
            except (CloudError,ProjectError,OSError) as error:
                logger.warning('cloud_download_target_check_failed error_type=%s',type(error).__name__)
                available=False
            if available:
                result['outcome']=meta.get('outcome','written')
            else:
                result.update(state='failed',receipt_state='completed',error=dict(
                    code='cloud_download_target_changed',step='status',retryable=False,
                    reason='The saved file is missing or its contents changed.'))
                result['progress']['saved_bytes']=0
                result.pop('path',None)
        return result

    async def download(self,sid,cid,plan,*,resume=False,verify=None):
        manager,domain = self.manager,self.domain
        key = (domain.project_id,cid)
        with manager.operation(),domain.owner.operation(),domain.materials.files.use():
            await domain.session(sid)
            try:
                previous = await run_blocking(self.plan,sid,cid)
            except CloudError as error:
                if resume or error.code!='cloud_delivery_not_found':raise
                previous = None
            receive_id=cid
            if previous is not None:
                if any(previous.get(k)!=v for k,v in plan.items()):
                    raise CloudError('cloud_delivery_request_conflict')
                plan = previous
            else:
                cached=await run_blocking(self._cached,sid,plan['binding_id'],plan['result_id'],plan['artifact_id'])
                if cached is not None:
                    receive_id,artifact=cached
                else:
                    fixed = await self.result(sid,plan['binding_id'],plan['result_id'])
                    artifact = next((f.artifact for f in fixed.bundle.files
                                     if f.role=='artifact' and f.artifact.artifact_id==plan['artifact_id']),None)
                    if artifact is None:raise CloudError('result_file_not_found',404)
                plan = plan | dict(size=artifact.size,sha256=artifact.sha256)
            async with manager.admission:
                if previous is None:
                    try:
                        existing=await run_blocking(self.plan,sid,cid)
                    except CloudError as error:
                        if error.code!='cloud_delivery_not_found':raise
                    else:
                        if existing!=plan:raise CloudError('cloud_delivery_request_conflict')
                        previous=existing
                if key not in manager.result_tool_flights:
                    if len(manager.result_tool_flights)>=manager.settings.receive_limits.max_active:
                        raise CloudError('cloud_delivery_capacity',429)
                    if previous is None:
                        await run_blocking(self._reserve,sid,cid,plan,verify,receive_id)
                    record,_ = await run_blocking(domain.deliveries.read,cid,sid)
                    if record.phase!='completed' and (resume or previous is None):
                        job = asyncio.create_task(self._produce(sid,cid,plan,resume,verify))
                        manager.result_tool_flights[key]=job
                        domain.own(job)
                        job.add_done_callback(lambda _:manager.result_tool_flights.pop(key,None))
            return (await self.status(sid,cid)) | {'action':'download'}

    def _reserve(self,sid,cid,plan,verify,receive_id=None):
        domain = self.domain
        path = Path(plan['path'])
        if not path.is_absolute() or not path.name:raise CloudError('cloud_delivery_path_unsafe')
        if verify is not None and verify()!=str(path):raise CloudError('cloud_delivery_root_changed')
        root = path.parent
        receive_id=receive_id or cid
        try:
            expected=directory_identity(root,directory_facts(root))
        except ProjectError as error:
            raise CloudError(error.code) from None
        request = DeliveryRequest(command_id=cid,local_session_id=sid,receive_id=receive_id,action='files',
            destination_handle=domain.project_id,destination_path=str(root),
            expected_directory=expected,
            target_names={plan['artifact_id']:path.name})
        _,signature = self.manager.deliveries.root(domain,request)
        record = DeliveryRecord(command_id=cid,local_project_id=domain.project_id,local_session_id=sid,
            receive_id=receive_id,action='files',phase='preparing',destination_path=str(root))
        domain.deliveries.reserve(request,record,dict(root_identity=signature,tool_download=plan),
                                  self.manager.settings.receive_limits.max_results)

    async def _produce(self,sid,cid,plan,resume,verify):
        manager,domain = self.manager,self.domain
        with domain.owner.operation(),domain.materials.files.use():
            try:
                record,meta = await run_blocking(domain.deliveries.read,cid,sid)
                receive_id=record.receive_id
                if receive_id!=cid:
                    receive=await manager.receives.read(domain,receive_id,sid)
                elif resume:
                    try:
                        receive = await manager.receives.retry(domain,cid,sid)
                    except CloudError as error:
                        if error.code!='cloud_receive_not_found':raise
                        receive = None
                else:
                    receive = None
                if receive is None:
                    await manager.receives.submit(domain,plan['binding_id'],plan['result_id'],
                        dict(command_id=cid,local_session_id=sid,artifact_ids=[plan['artifact_id']],include_code=False))
                key=(domain.project_id,cid)
                job = manager.receives.flights.get((domain.project_id,receive_id))
                if job is not None:await asyncio.shield(job)
                receive = await manager.receives.read(domain,receive_id,sid)
                if receive.phase!='available':
                    raise CloudError(receive.error_code or 'cloud_delivery_receive_unavailable')
                if ((receive.binding_id,receive.result_id)!=(plan['binding_id'],plan['result_id'])
                        or not any(f.role=='artifact' and f.verified and
                                   (f.artifact.artifact_id,f.artifact.size,f.artifact.sha256)==
                                   (plan['artifact_id'],plan['size'],plan['sha256']) for f in receive.files)):
                    raise CloudError('cloud_result_identity_mismatch')
                if verify is not None and record.phase not in ('executing','partial'):
                    if await run_blocking(verify)!=plan['path']:raise CloudError('cloud_delivery_root_changed')
                if not meta.get('verified'):
                    await manager.deliveries.prepare(domain,meta['request'],tool_plan=plan)
                    job=manager.deliveries.flights.get(key)
                    if job is not None:await asyncio.shield(job)
                record,_ = await run_blocking(domain.deliveries.read,cid,sid)
                if record.phase=='failed' and not (resume and meta.get('verified') and record.error_code in RETRYABLE):return
                if record.conflicts and not plan['force']:return
                if verify is not None and record.phase=='prepared':
                    if await run_blocking(verify)!=plan['path']:raise CloudError('cloud_delivery_root_changed')
                await manager.deliveries.execute(domain,cid,sid,force=plan['force'],tool_plan=plan)
                job=manager.deliveries.flights.get(key)
                if job is not None:await asyncio.shield(job)
            except Exception as error:
                code = error.code if isinstance(error,(CloudError,ProjectError)) else 'cloud_download_failed'
                logger.warning('cloud_download_failed code=%s error_type=%s',code,type(error).__name__)
                record,_ = await run_blocking(domain.deliveries.read,cid,sid)
                if record.phase!='completed':
                    phase='partial' if record.phase in ('executing','partial') else 'failed'
                    await run_blocking(domain.deliveries.save,record.model_copy(update={'phase':phase,'error_code':code}))
