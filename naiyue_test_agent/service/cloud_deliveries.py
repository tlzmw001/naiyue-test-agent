"""Local write authorization is explicit and independent of cloud execution grants."""
from fastapi import Query, Request
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.cloud_tasks import UUIDText,CloudError
from naiyue_test_agent.contracts.cloud_local_tasks import SessionId
from naiyue_test_agent.contracts.cloud_delivery import DeliveryRequest,DeliveryConfirm
from naiyue_test_agent.contracts.projects import LocalSource


def register(router,access,domain,body,command_response):
    def trusted(project):
        if not project.trusted:raise CloudError('cloud_project_trust_required',403)
        if not isinstance(project.source,LocalSource):raise CloudError('cloud_local_source_required')

    def files_only(action):
        if action!='files':raise CloudError('cloud_result_workflow_retired',410)

    @router.post('/cloud-deliveries')
    async def prepare(request:Request,local_project_id:UUIDText):
        manager=access(request);value=await body(request,DeliveryRequest)
        project,local=await domain(request,local_project_id,value.local_session_id);trusted(project)
        files_only(value.action)
        record=await manager.deliveries.prepare(local,value.model_dump(mode='json'))
        return command_response(request,record.model_dump(mode='json'),status_code=202)

    @router.get('/cloud-legacy-deliveries')
    async def legacy(request:Request,local_project_id:UUIDText,local_session_id:SessionId,
                     offset:int=Query(default=0,ge=0,le=1000000),limit:int=Query(default=20,ge=1,le=100)):
        access(request);project,local=await domain(request,local_project_id,local_session_id)
        trusted(project)
        from naiyue_test_agent.application.projects.locations import directory_facts
        from naiyue_test_agent.contracts.projects import ProjectError

        def summarize():
            rows=local.deliveries.legacy_history(local_session_id,offset=offset,limit=limit)
            if not rows:return []
            root=project.source.path
            try:identity=directory_facts(root)
            except ProjectError:identity=None
            result=[]
            for record,meta in rows:
                recovery=record.phase in ('executing','partial','unknown')
                target=record.destination_path
                if not target and identity is not None and identity==meta.get('root_identity'):
                    target=str(root/record.name) if record.action=='export' else str(root)
                result.append(dict(command_id=record.command_id,action=record.action,phase=record.phase,
                    path=target,done=record.done,recovery_required=recovery,
                    evidence_path=str(local.materials.files.root/'deliveries'/record.command_id) if recovery else None))
            return result

        with local.owner.operation():return await run_blocking(summarize)

    @router.get('/cloud-deliveries/{command_id}')
    async def status(request:Request,command_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        record=await manager.deliveries.read(local,command_id,local_session_id)
        return command_response(request,record.model_dump(mode='json'))

    @router.post('/cloud-deliveries/{command_id}/execute')
    async def execute(request:Request,command_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);confirmation=await body(request,DeliveryConfirm)
        project,local=await domain(request,local_project_id,local_session_id);trusted(project)
        prior,_=await run_blocking(local.deliveries.read,command_id,local_session_id)
        files_only(prior.action)
        record=await manager.deliveries.execute(local,command_id,local_session_id,force=confirmation.force)
        return command_response(request,record.model_dump(mode='json'),status_code=202)

    @router.post('/cloud-deliveries/{command_id}/recheck')
    async def recheck(request:Request,command_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request)
        project,local=await domain(request,local_project_id,local_session_id);trusted(project)
        prior,_=await run_blocking(local.deliveries.read,command_id,local_session_id)
        files_only(prior.action)
        record=await manager.deliveries.recheck(local,command_id,local_session_id)
        return command_response(request,record.model_dump(mode='json'),status_code=202)
