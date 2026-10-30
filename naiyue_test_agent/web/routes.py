"""One set of HTTP session/run routes, bound by the current request's service."""
from fastapi import APIRouter, Depends, Query,Request
from fastapi.responses import Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from .dependencies import get_service
from .errors import WebError
from .schemas import (ActiveToolsInput, ApprovalInput, ApprovalStopInput, CompactInput,
    ControlInput, MessageInput, NewSession, PermissionModeInput, RecoveryInput,
    RegenerateInput, SessionOrder, SessionPresentation, ThinkingInput, MCPRefreshInput,EnvironmentActionInput)
from .background_tasks import router as background_task_router
from .result_routes import router as result_router


def create_api_router():
    router = APIRouter()
    @router.get('/models')
    async def models(service=Depends(get_service)):
        return service.models()

    @router.get('/service')
    async def service_info(service=Depends(get_service)):
        return service.service_info()

    @router.get('/session-controls')
    async def session_control_options(service=Depends(get_service)):
        return service.session_control_options()

    @router.post('/mcp/refresh')
    async def refresh_mcp(body: MCPRefreshInput | None = None, service=Depends(get_service)):
        return await service.refresh_mcp()

    @router.get('/skills')
    async def skills(session_id: str | None=Query(default=None,min_length=1,max_length=128),service=Depends(get_service)):
        return await service.skills(session_id)

    @router.get('/sessions')
    async def sessions(service=Depends(get_service)):
        return await service.sessions()

    @router.put('/session-order')
    async def reorder_sessions(body: SessionOrder, service=Depends(get_service)):
        return await service.reorder_sessions(body)

    @router.patch('/sessions/{session_id}/presentation')
    async def update_presentation(session_id: str, body: SessionPresentation, service=Depends(get_service)):
        return await service.update_presentation(session_id, body)

    @router.delete('/sessions/{session_id}')
    async def delete_session(session_id: str, service=Depends(get_service)):
        return await service.delete_session(session_id)

    @router.post('/sessions', status_code=201)
    async def create(body: NewSession, request: Request, service=Depends(get_service)):
        if body.execution=='sandbox':
            sid=await service.application.sessions.create_sandbox(body.model_id,
                request_id=request.headers['x-request-id'],include_untracked=body.include_untracked,
                profile_id=body.profile_id,expected_profile_revision=body.expected_profile_revision,wait_for_ready=body.wait_for_ready)
            return {'session_id':sid}
        return await service.create_session(body.model_id)

    @router.post('/sessions/{session_id}/environment/prepare')
    async def prepare_environment(session_id:str,service=Depends(get_service)):
        return await service.application.sessions.prepare_environment(session_id)

    @router.post('/sessions/{session_id}/environment/{action}')
    async def environment_action(session_id:str,action:str,body:EnvironmentActionInput|None=None,service=Depends(get_service)):
        return await service.application.sessions.environment_action(session_id,action,
            confirm_destroy=body.confirm_destroy if body is not None else False)

    @router.get('/sessions/{session_id}')
    async def session(session_id: str, service=Depends(get_service)):
        return await service.session(session_id)

    @router.post('/sessions/{session_id}/prepare')
    async def prepare(session_id: str, service=Depends(get_service)):
        return await service.prepare(session_id)

    @router.post('/sessions/{session_id}/regenerate')
    async def regenerate(session_id: str, body: RegenerateInput, service=Depends(get_service)):
        return await service.regenerate(session_id, body)

    @router.get('/sessions/{session_id}/messages')
    async def messages(session_id: str, before: str | None = Query(default=None, max_length=4096),
                       agent_id: str | None = Query(default=None, max_length=128),
                       limit: int | None = Query(default=None, ge=1, le=256), service=Depends(get_service)):
        return await service.messages(session_id, agent_id=agent_id, before=before, limit=limit)

    @router.get('/sessions/{session_id}/messages/{message_id}/images/{index}')
    async def message_image(session_id: str, message_id: str, index: int,
                            source_revision: str = Query(pattern='^[0-9a-f]{64}$'),
                            conversation_revision: int = Query(ge=0), service=Depends(get_service)):
        try:
            data, mime = await service.application.history.image(session_id, message_id, index,
                source_revision=source_revision, conversation_revision=conversation_revision)
        except Exception as error:
            from naiyue_test_agent.storage.errors import SessionPersistenceError
            if isinstance(error,SessionPersistenceError) and error.args == ('session_not_found',):
                raise WebError('image_not_found',404) from None
            from .errors import public_error
            raise public_error(error) from None
        return Response(data,media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.get('/sessions/{session_id}/messages/{sequence}/content')
    async def message_content(session_id: str, sequence: int, revision: int = Query(ge=1),
                              conversation_revision: int = Query(default=0, ge=0), offset: int = Query(default=0, ge=0),
                              agent_id: str | None = Query(default=None, max_length=128), service=Depends(get_service)):
        if sequence < 1:
            raise WebError('invalid_request', 422)
        return await service.message_content(session_id, sequence, agent_id=agent_id,
                                                        revision=revision, offset=offset, conversation_revision=conversation_revision)

    @router.get('/sessions/{session_id}/messages/{sequence}/reasoning')
    async def message_reasoning(session_id: str, sequence: int, revision: int = Query(ge=1),
                                conversation_revision: int = Query(ge=0), offset: int = Query(default=0, ge=0),
                                agent_id: str | None = Query(default=None, max_length=128), service=Depends(get_service)):
        if sequence < 1:
            raise WebError('invalid_request', 422)
        return await service.message_reasoning(session_id, sequence, revision=revision, offset=offset,
            conversation_revision=conversation_revision, agent_id=agent_id)

    @router.get('/sessions/{session_id}/messages/{sequence}/change')
    async def file_change(session_id: str, sequence: int, revision: int = Query(ge=1),
                          conversation_revision: int = Query(default=0, ge=0),
                          agent_id: str | None = Query(default=None, max_length=128), service=Depends(get_service)):
        if sequence < 1:
            raise WebError('invalid_request', 422)
        return await service.file_change(session_id, sequence, revision=revision, agent_id=agent_id, conversation_revision=conversation_revision)

    @router.get('/sessions/{session_id}/messages/{sequence}/patch')
    async def file_patch(session_id: str, sequence: int, revision: int = Query(ge=1),
                         conversation_revision: int = Query(default=0, ge=0),
                         agent_id: str | None = Query(default=None, max_length=128), service=Depends(get_service)):
        if sequence < 1:
            raise WebError('invalid_request', 422)
        text = await service.file_change(session_id, sequence, revision=revision, agent_id=agent_id, patch=True, conversation_revision=conversation_revision)
        return Response(text, media_type='text/x-diff', headers={'Content-Disposition': 'attachment; filename="change.patch"'})

    @router.get('/sessions/{session_id}/subagents')
    async def subagents(session_id: str, service=Depends(get_service)):
        return await service.subagents(session_id)

    @router.get('/sessions/{session_id}/context')
    async def context(session_id: str, service=Depends(get_service)):
        return await service.context(session_id)

    @router.get('/sessions/{session_id}/usage')
    async def usage(session_id: str, after_model: str | None = Query(default=None, min_length=1, max_length=128), service=Depends(get_service)):
        return await service.usage(session_id, after_model=after_model)

    @router.get('/sessions/{session_id}/diagnostics')
    async def diagnostic_runs(session_id: str, before: int | None = Query(default=None, ge=1, le=2**63 - 1), service=Depends(get_service)):
        return await service.diagnostics(session_id, before=before)

    @router.get('/sessions/{session_id}/diagnostics/{run_id}')
    async def diagnostic_events(session_id: str, run_id: str,
                                after: int = Query(default=0, ge=0, le=2**63 - 1), service=Depends(get_service)):
        return await service.diagnostics(session_id, run_id=run_id, after=after)

    @router.get('/sessions/{session_id}/subagents/{agent_id}')
    async def subagent(session_id: str, agent_id: str, service=Depends(get_service)):
        return await service.subagent(session_id, agent_id)

    @router.patch('/sessions/{session_id}/model')
    async def select(session_id: str, body: NewSession, service=Depends(get_service)):
        return await service.select_model(session_id, body.model_id)

    @router.patch('/sessions/{session_id}/thinking')
    async def thinking(session_id: str, body: ThinkingInput, service=Depends(get_service)):
        return await service.select_thinking(session_id, body.model_id, body.thinking_level)

    @router.post('/sessions/{session_id}/compact', status_code=202)
    async def compact(session_id: str, body: CompactInput, service=Depends(get_service)):
        return await service.compact(session_id, body)

    @router.patch('/sessions/{session_id}/tools')
    async def select_tools(session_id: str, body: ActiveToolsInput, service=Depends(get_service)):
        return await service.select_tools(session_id, body.active_tools)

    @router.patch('/sessions/{session_id}/permission-mode')
    async def select_permission_mode(session_id: str, body: PermissionModeInput, service=Depends(get_service)):
        return await service.select_permission_mode(session_id, body.permission_mode)

    @router.post('/sessions/{session_id}/runs', status_code=202)
    async def start(session_id: str, body: MessageInput, service=Depends(get_service)):
        return await service.start(session_id, body)

    @router.post('/sessions/{session_id}/submissions/{request_id}/stop')
    async def stop_submission(session_id:str,request_id:str,service=Depends(get_service)):
        return await service.stop_submission(session_id,request_id)

    @router.post('/sessions/{session_id}/approvals', status_code=202)
    async def approve(session_id: str, body: ApprovalInput, service=Depends(get_service)):
        return await service.approve(session_id, body)

    @router.post('/sessions/{session_id}/approvals/stop', status_code=202)
    async def stop_approval(session_id: str, body: ApprovalStopInput, service=Depends(get_service)):
        return await service.stop_approval(session_id, body)

    @router.post('/sessions/{session_id}/recover')
    async def recover(session_id: str, body: RecoveryInput, service=Depends(get_service)):
        return await service.recover(session_id, body)

    @router.get('/runs/{run_id}')
    async def run(run_id: str, service=Depends(get_service)):
        return service.delivery.get(run_id).view()

    async def stream_job(run_id: str, after: int = Query(default=0, ge=0), service=Depends(get_service)):
        job = service.delivery.get(run_id)
        job.check_cursor(after)
        return job, after

    @router.get('/runs/{run_id}/events', response_class=EventSourceResponse)
    async def events(stream_args=Depends(stream_job), service=Depends(get_service)):
        job, after = stream_args
        async for event in job.stream(after):
            yield ServerSentEvent(**event)

    @router.post('/runs/{run_id}/controls', status_code=202)
    async def control(run_id: str, body: ControlInput, service=Depends(get_service)):
        return await service.delivery.control(run_id, body)

    router.include_router(background_task_router)
    router.include_router(result_router)
    from .image_queue_routes import router as image_queue_router
    router.include_router(image_queue_router)
    return router
