"""Authenticated local stream/detail routes; no browser-selected remote endpoint."""
import asyncio
import logging
from starlette.responses import StreamingResponse
from fastapi import Request,Query
from naiyue_test_agent.agent_runtime.async_utils import await_settled,run_blocking
from naiyue_test_agent.application.cloud_local.observation import observe
from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText
from naiyue_test_agent.contracts.cloud_local_tasks import SessionId
from naiyue_test_agent.contracts.cloud_history import Identity

logger=logging.getLogger(__name__)


class ClientObservationResponse(StreamingResponse):
    def __init__(self,body,settings):
        super().__init__(body,media_type='text/event-stream',headers={'Cache-Control':'private, no-store',
            'X-Accel-Buffering':'no','X-Content-Type-Options':'nosniff'})
        self.settings=settings

    async def __call__(self,scope,receive,send):
        primary=None
        async def bounded(message):
            async with asyncio.timeout(self.settings.stream_send_timeout_seconds):await send(message)
        try:
            async with asyncio.timeout(self.settings.stream_seconds):await super().__call__(scope,receive,bounded)
        except TimeoutError as error:
            logger.warning('cloud_local_stream_timeout')
            primary=error
            raise RuntimeError('cloud_local_stream_timeout') from error
        except BaseException as error:primary=error;raise
        finally:
            try:await await_settled(self.body_iterator.aclose())
            except BaseException as error:
                logger.warning('cloud_local_stream_close_failed error_type=%s',type(error).__name__)
                if primary is None:raise


def register(router,access,domain,bounded_view):
    @router.get('/cloud-tasks/{binding_id}/stream')
    async def stream(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId,
            thread_id:Identity|None=None):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        if len(request.headers.getlist('last-event-id'))>1:raise CloudError('invalid_headers',400)
        with local.owner.operation():await run_blocking(local.repo.binding,binding_id,local_session_id)
        return ClientObservationResponse(observe(manager,local,binding_id,local_session_id,thread_id=thread_id,
            last_event_id=request.headers.get('last-event-id'),max_frame_bytes=request.app.state.web_settings.max_frame_bytes),manager.settings)

    @router.get('/cloud-tasks/{binding_id}/history/threads')
    async def threads(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId,
            cursor:str|None=Query(default=None,max_length=16384),limit:int=Query(default=50,ge=1,le=100)):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        params={'limit':limit}
        if cursor is not None:params['cursor']=cursor
        return await bounded_view(request,manager,local,binding_id,local_session_id,'history/threads',params)

    @router.get('/cloud-tasks/{binding_id}/history/messages/{message_id}')
    async def detail(request:Request,binding_id:UUIDText,message_id:Identity,local_project_id:UUIDText,
            local_session_id:SessionId,thread_id:Identity,revision:int=Query(ge=1),offset:int=Query(default=0,ge=0),
            limit:int=Query(default=16384,ge=1,le=65536),field:str=Query(default='content',pattern='^(content|reasoning|tool_calls|file_change|mcp_result)$')):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        return await bounded_view(request,manager,local,binding_id,local_session_id,'history/messages',
            dict(message_id=message_id,thread_id=thread_id,revision=revision,offset=offset,limit=limit,field=field))
