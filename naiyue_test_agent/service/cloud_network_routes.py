"""Page-owned service targets; profile/ports stay administrator controlled."""
from fastapi import Request
from pydantic import Field
from naiyue_test_agent.contracts.cloud_tasks import CloudRecord
from naiyue_test_agent.contracts.cloud_networks import ServiceTarget


class ChangeTarget(CloudRecord):
    target:ServiceTarget
    expected_revision:int=Field(ge=0)


def register(router,access,body):
    @router.get('/cloud-networks/{profile_id}')
    async def status(request:Request,profile_id:str):
        manager=access(request)
        with manager.operation():return await manager.networks.read(profile_id)

    @router.post('/cloud-networks/{profile_id}/test')
    async def test(request:Request,profile_id:str):
        manager=access(request);value=await body(request,ServiceTarget)
        with manager.operation():return await manager.networks.test(profile_id,value)

    @router.post('/cloud-networks/{profile_id}/cancel')
    async def cancel(request:Request,profile_id:str):
        manager=access(request);await body(request,CloudRecord)
        with manager.operation():return await manager.networks.cancel(profile_id)

    @router.post('/cloud-networks/{profile_id}/{slot}')
    @router.post('/cloud-networks/{profile_id}/entries/{slot}')
    async def change(request:Request,profile_id:str,slot:str):
        manager=access(request);value=await body(request,ChangeTarget)
        with manager.operation():return await manager.networks.change(profile_id,slot,value.target,value.expected_revision)
