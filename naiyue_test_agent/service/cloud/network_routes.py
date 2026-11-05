"""Owner-only network switching; no client-controlled dial address on Coordinator."""
import asyncio
import logging
from fastapi import Request
from naiyue_test_agent.contracts.cloud_networks import NetworkChange,NetworkFinish
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cloud.networks import NetworkRepository
from naiyue_test_agent.application.cloud.work import cloud_work
from .auth import owner_authorize
from .http import _body

logger=logging.getLogger(__name__)


def register(owner,runtime):
    repo=NetworkRepository(runtime.database)
    def endpoint(ident):
        value=runtime.settings.networks.get(ident)
        if value is None:raise CloudError('cloud_network_not_configured',404)
        return value

    @owner.get('/api/cloud/v1/networks/{ident}')
    async def read(request:Request,ident:str):
        owner_authorize(request,runtime);config=endpoint(ident)
        return dict(endpoints=config.model_dump(),state=await cloud_work(runtime,lambda:repo.read(ident)))

    @owner.post('/api/cloud/v1/networks/{ident}/prepare')
    async def prepare(request:Request,ident:str):
        owner_authorize(request,runtime);endpoint(ident)
        value=NetworkChange.model_validate(await _body(request,runtime))
        endpoint(ident).port(value.slot)
        return await cloud_work(runtime,lambda:repo.begin(ident,value))

    @owner.post('/api/cloud/v1/networks/{ident}/finish')
    async def finish(request:Request,ident:str):
        owner_authorize(request,runtime);endpoint(ident)
        value=NetworkFinish.model_validate(await _body(request,runtime))
        def finish_current():
            pending=repo.read(ident)['switching']
            if pending:endpoint(ident).port(pending['slot'])
            return repo.finish(ident,value.command_id)
        return await cloud_work(runtime,finish_current)

    @owner.post('/api/cloud/v1/networks/{ident}/cancel')
    async def cancel(request:Request,ident:str):
        owner_authorize(request,runtime);endpoint(ident)
        value=NetworkFinish.model_validate(await _body(request,runtime))
        return await cloud_work(runtime,lambda:repo.cancel(ident,value.command_id))

    @owner.post('/api/cloud/v1/networks/{ident}/probe/{slot}')
    async def probe(request:Request,ident:str,slot:str):
        owner_authorize(request,runtime);config=endpoint(ident)
        port=config.port(slot)
        if await _body(request,runtime)!={}:raise CloudError('invalid_request',422)
        try:
            _,writer=await asyncio.wait_for(asyncio.open_connection(config.bind_host,port),
                timeout=min(5,runtime.settings.control_io_timeout_seconds))
            writer.close();await writer.wait_closed()
        except (OSError,TimeoutError) as error:
            logger.warning('cloud_network_probe_failed error_type=%s',type(error).__name__)
            raise CloudError('cloud_network_unreachable',503) from None
        return dict(tcp_reachable=True)
