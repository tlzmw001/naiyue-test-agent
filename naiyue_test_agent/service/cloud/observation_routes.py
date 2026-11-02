"""Owner-only live/usage endpoints and bounded streaming response ownership."""
import asyncio
import logging
from fastapi import Request
from starlette.responses import StreamingResponse

from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .auth import owner_authorize

logger=logging.getLogger(__name__)


class ObservationStreamTimeout(RuntimeError):
    """Keep a post-header stream timeout out of the storage OSError handler."""


class ObservationResponse(StreamingResponse):
    def __init__(self,lease):
        self.lease=lease
        super().__init__(lease.chunks(),media_type='text/event-stream',headers={
            'Cache-Control':'private, no-store','X-Accel-Buffering':'no','X-Content-Type-Options':'nosniff'})

    async def __call__(self,scope,receive,send):
        async def timed_send(message):
            try:
                async with asyncio.timeout(self.lease.service.limits.send_timeout_seconds):await send(message)
            except TimeoutError as error:
                raise ObservationStreamTimeout('observation_send_timeout') from error
        try:
            async with asyncio.timeout_at(self.lease.deadline):await super().__call__(scope,receive,timed_send)
        except TimeoutError as error:
            logger.info('cloud_observation_stream_timeout')
            raise ObservationStreamTimeout('observation_stream_deadline') from error
        except ObservationStreamTimeout:
            logger.info('cloud_observation_stream_timeout')
            raise
        finally:
            await await_settled(self.lease.aclose())
            await await_settled(self.body_iterator.aclose())


def register_observation_routes(owner,runtime):
    base='/api/cloud/v1/tasks/{task_id}'

    @owner.get(base+'/snapshot')
    async def snapshot(request:Request,task_id:str,thread_id:str|None=None,limit:int|None=None):
        owner_authorize(request,runtime,query_fields=('thread_id','limit'))
        return await runtime.observations.snapshot(task_id,thread_id=thread_id,limit=limit)

    @owner.get(base+'/usage')
    async def usage(request:Request,task_id:str,limit:int|None=None,after_model:str|None=None):
        owner_authorize(request,runtime,query_fields=('limit','after_model'))
        return await runtime.observations.usage(task_id,limit=limit,after_model=after_model)

    @owner.get(base+'/stream')
    async def stream(request:Request,task_id:str,thread_id:str|None=None):
        owner_authorize(request,runtime,query_fields=('thread_id',))
        if len(request.headers.getlist('last-event-id'))>1:raise CloudError('invalid_headers',400)
        lease=await runtime.observations.subscribe(task_id,thread_id=thread_id,last_event_id=request.headers.get('last-event-id'))
        return ObservationResponse(lease)
