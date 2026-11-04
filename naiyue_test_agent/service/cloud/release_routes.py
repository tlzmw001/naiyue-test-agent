"""Release/maintenance mutation is exclusively on the authenticated owner listener."""
from fastapi import Request
from .auth import owner_authorize
from .http import _body
from naiyue_test_agent.application.cloud.work import cloud_work
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def register_release_routes(owner,runtime):
    service=runtime.releases;base='/api/cloud/v1'

    @owner.get(base+'/releases')
    async def releases(request:Request):
        owner_authorize(request,runtime)
        return await cloud_work(runtime,service.repository.list)

    @owner.get(base+'/releases/active/{template_id}')
    async def active(request:Request,template_id:str):
        owner_authorize(request,runtime)
        return await cloud_work(runtime,lambda:service.active(template_id))

    @owner.get(base+'/releases/{release_id}/{template_id}')
    async def ready(request:Request,release_id:str,template_id:str):
        owner_authorize(request,runtime)
        return await cloud_work(runtime,lambda:service.get(release_id,template_id))

    @owner.post(base+'/releases/register')
    async def register(request:Request):
        owner_authorize(request,runtime);body=await _body(request,runtime)
        return await cloud_work(runtime,lambda:service.register(body))

    @owner.post(base+'/releases/activate')
    async def activate(request:Request):
        owner_authorize(request,runtime);body=await _body(request,runtime)
        return await cloud_work(runtime,lambda:service.activate(body))

    @owner.get(base+'/maintenance')
    async def maintenance_status(request:Request):
        owner_authorize(request,runtime)
        return await cloud_work(runtime,service.maintenance_status)

    @owner.post(base+'/maintenance')
    async def maintenance(request:Request):
        owner_authorize(request,runtime);body=await _body(request,runtime)
        return await cloud_work(runtime,lambda:service.maintenance(body))

    @owner.post(base+'/tasks/{task_id}/upgrade')
    async def upgrade(request:Request,task_id:str):
        owner_authorize(request,runtime);body=await _body(request,runtime)
        if runtime.lifecycle is None:raise CloudError('cloud_backend_not_configured',503)
        from naiyue_test_agent.application.cloud.upgrades import reserve_upgrade
        return await reserve_upgrade(runtime,task_id,body)

    @owner.post(base+'/tasks/{task_id}/upgrade/commit')
    async def commit(request:Request,task_id:str):
        owner_authorize(request,runtime);body=await _body(request,runtime)
        from naiyue_test_agent.application.cloud.upgrades import commit_upgrade
        return await cloud_work(runtime,lambda:commit_upgrade(runtime,task_id,body))

    @owner.post(base+'/tasks/{task_id}/upgrade/cancel')
    async def cancel(request:Request,task_id:str):
        owner_authorize(request,runtime);body=await _body(request,runtime)
        from naiyue_test_agent.application.cloud.upgrades import cancel_upgrade
        return await cloud_work(runtime,lambda:cancel_upgrade(runtime,task_id,body))
