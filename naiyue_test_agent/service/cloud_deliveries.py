"""Local write authorization is explicit and independent of cloud execution grants."""
from fastapi import Request
from naiyue_test_agent.contracts.cloud_tasks import UUIDText,CloudError
from naiyue_test_agent.contracts.cloud_local_tasks import SessionId
from naiyue_test_agent.contracts.cloud_delivery import DeliveryRequest,DeliveryConfirm
from naiyue_test_agent.contracts.projects import LocalSource


def register(router,access,domain,body,command_response):
    def trusted(project):
        if not project.trusted:raise CloudError('cloud_project_trust_required',403)
        if not isinstance(project.source,LocalSource):raise CloudError('cloud_local_source_required')

    @router.post('/cloud-deliveries')
    async def prepare(request:Request,local_project_id:UUIDText):
        manager=access(request);value=await body(request,DeliveryRequest)
        project,local=await domain(request,local_project_id,value.local_session_id);trusted(project)
        record=await manager.deliveries.prepare(local,value.model_dump(mode='json'))
        return command_response(request,record.model_dump(mode='json'),status_code=202)

    @router.get('/cloud-deliveries/{command_id}')
    async def status(request:Request,command_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        record=await manager.deliveries.read(local,command_id,local_session_id)
        return command_response(request,record.model_dump(mode='json'))

    @router.post('/cloud-deliveries/{command_id}/execute')
    async def execute(request:Request,command_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);confirmation=await body(request,DeliveryConfirm)
        project,local=await domain(request,local_project_id,local_session_id);trusted(project)
        record=await manager.deliveries.execute(local,command_id,local_session_id,force=confirmation.force)
        return command_response(request,record.model_dump(mode='json'),status_code=202)

    @router.post('/cloud-deliveries/{command_id}/recheck')
    async def recheck(request:Request,command_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request)
        project,local=await domain(request,local_project_id,local_session_id);trusted(project)
        record=await manager.deliveries.recheck(local,command_id,local_session_id)
        return command_response(request,record.model_dump(mode='json'),status_code=202)
