"""Local HTTP boundary; the lifespan owns one existing async application."""

from contextlib import AsyncExitStack, asynccontextmanager
import logging

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.staticfiles import StaticFiles

from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.settings import load_settings
from naiyue_test_agent.model_access import ModelConfigError
from .auth import authorize
from .errors import WebError
from .run_delivery import RunDelivery
from .schemas import ActiveToolsInput, ApprovalInput, ApprovalStopInput, CompactInput, ControlInput, MessageInput, NewSession, PermissionModeInput, RecoveryInput, RegenerateInput, SessionOrder, SessionPresentation, ThinkingInput
from .service import WebService
from .headers import LocalHeaders
from .windows_disconnect import windows_disconnect_handler
from .schemas import MCPRefreshInput


logger = logging.getLogger(__name__)


def create_web_app(settings, *, application_factory=None):
    @asynccontextmanager
    async def lifespan(app):
        tool_settings = settings.runtime_settings if settings.runtime_settings is not None else load_settings()
        delivery_settings = settings if 'event_cache_bytes' in settings.model_fields_set else settings.model_copy(
            update={'event_cache_bytes': tool_settings.web_event_cache_bytes})
        tools = create_builtin_tools(tool_settings.tools, process_settings=tool_settings.process, output_settings=tool_settings.tool_output, progress_settings=tool_settings.progress, network_settings=tool_settings.network, retry_settings=tool_settings.tool_retry)
        initial_tools = []
        if settings.workspace_root is not None:
            initial_tools = tool_settings.default_active_tools
            if initial_tools is None:
                initial_tools = [spec.tool.name for spec in tools if spec.tool.name not in ('bash', 'powershell')]
        factory = application_factory or (lambda: open_async_persistent_application(
            settings.config_path, database_path=settings.database_path, tools=tools,
            system_prompt=BASE_SYSTEM_PROMPT, response_format=None, initial_active_tools=initial_tools, settings=tool_settings,
            workspace_binding={'workspace_id': settings.workspace_id, 'root_path': str(settings.workspace_root)}
                if settings.workspace_id is not None else None))
        async with AsyncExitStack() as stack:
            stack.enter_context(windows_disconnect_handler())
            try:
                application = await stack.enter_async_context(factory())
            except ModelConfigError as error:
                app.state.startup_error = error
                raise
            delivery = RunDelivery(application, delivery_settings, progress_settings=tool_settings.progress)
            app.state.delivery = delivery
            app.state.service = WebService(application, delivery, delivery_settings)
            try:
                yield
            finally:
                await delivery.close()

    async def access(request: Request):
        authorize(request, settings)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(access)])
    app.add_middleware(LocalHeaders)

    @app.exception_handler(WebError)
    async def web_error(request, error):
        return JSONResponse({'error': {'code': error.code}}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        logger.warning('web_request_invalid')
        return JSONResponse({'error': {'code': 'invalid_request'}}, status_code=422)

    @app.get('/api/models')
    async def models():
        return app.state.service.models()

    @app.get('/api/service')
    async def service_info():
        return app.state.service.service_info()

    @app.get('/api/session-controls')
    async def session_control_options():
        return app.state.service.session_control_options()

    @app.post('/api/mcp/refresh')
    async def refresh_mcp(body: MCPRefreshInput | None = None):
        return await app.state.service.refresh_mcp()

    @app.get('/api/skills')
    async def skills():
        return await app.state.service.skills()

    @app.get('/api/sessions')
    async def sessions():
        return await app.state.service.sessions()

    @app.put('/api/session-order')
    async def reorder_sessions(body: SessionOrder):
        return await app.state.service.reorder_sessions(body)

    @app.patch('/api/sessions/{session_id}/presentation')
    async def update_presentation(session_id: str, body: SessionPresentation):
        return await app.state.service.update_presentation(session_id, body)

    @app.delete('/api/sessions/{session_id}')
    async def delete_session(session_id: str):
        return await app.state.service.delete_session(session_id)

    @app.post('/api/sessions', status_code=201)
    async def create(body: NewSession):
        return await app.state.service.create_session(body.model_id)

    @app.get('/api/sessions/{session_id}')
    async def session(session_id: str):
        return await app.state.service.session(session_id)

    @app.post('/api/sessions/{session_id}/prepare')
    async def prepare(session_id: str):
        return await app.state.service.prepare(session_id)

    @app.post('/api/sessions/{session_id}/regenerate')
    async def regenerate(session_id: str, body: RegenerateInput):
        return await app.state.service.regenerate(session_id, body)

    @app.get('/api/sessions/{session_id}/messages')
    async def messages(session_id: str, before: str | None = Query(default=None, max_length=4096),
                       agent_id: str | None = Query(default=None, max_length=128),
                       limit: int | None = Query(default=None, ge=1, le=256)):
        return await app.state.service.messages(session_id, agent_id=agent_id, before=before, limit=limit)

    @app.get('/api/sessions/{session_id}/messages/{sequence}/content')
    async def message_content(session_id: str, sequence: int, revision: int = Query(ge=1),
                              conversation_revision: int = Query(default=0, ge=0), offset: int = Query(default=0, ge=0),
                              agent_id: str | None = Query(default=None, max_length=128)):
        if sequence < 1:
            raise WebError('invalid_request', 422)
        return await app.state.service.message_content(session_id, sequence, agent_id=agent_id,
                                                        revision=revision, offset=offset, conversation_revision=conversation_revision)

    @app.get('/api/sessions/{session_id}/messages/{sequence}/reasoning')
    async def message_reasoning(session_id: str, sequence: int, revision: int = Query(ge=1),
                                conversation_revision: int = Query(ge=0), offset: int = Query(default=0, ge=0),
                                agent_id: str | None = Query(default=None, max_length=128)):
        if sequence < 1:
            raise WebError('invalid_request', 422)
        return await app.state.service.message_reasoning(session_id, sequence, revision=revision, offset=offset,
            conversation_revision=conversation_revision, agent_id=agent_id)

    @app.get('/api/sessions/{session_id}/messages/{sequence}/change')
    async def file_change(session_id: str, sequence: int, revision: int = Query(ge=1),
                          conversation_revision: int = Query(default=0, ge=0),
                          agent_id: str | None = Query(default=None, max_length=128)):
        if sequence < 1:
            raise WebError('invalid_request', 422)
        return await app.state.service.file_change(session_id, sequence, revision=revision, agent_id=agent_id, conversation_revision=conversation_revision)

    @app.get('/api/sessions/{session_id}/messages/{sequence}/patch')
    async def file_patch(session_id: str, sequence: int, revision: int = Query(ge=1),
                         conversation_revision: int = Query(default=0, ge=0),
                         agent_id: str | None = Query(default=None, max_length=128)):
        if sequence < 1:
            raise WebError('invalid_request', 422)
        text = await app.state.service.file_change(session_id, sequence, revision=revision, agent_id=agent_id, patch=True, conversation_revision=conversation_revision)
        return Response(text, media_type='text/x-diff', headers={'Content-Disposition': 'attachment; filename="change.patch"'})

    @app.get('/api/sessions/{session_id}/subagents')
    async def subagents(session_id: str):
        return await app.state.service.subagents(session_id)

    @app.get('/api/sessions/{session_id}/context')
    async def context(session_id: str):
        return await app.state.service.context(session_id)

    @app.get('/api/sessions/{session_id}/usage')
    async def usage(session_id: str, after_model: str | None = Query(default=None, min_length=1, max_length=128)):
        return await app.state.service.usage(session_id, after_model=after_model)

    @app.get('/api/sessions/{session_id}/diagnostics')
    async def diagnostic_runs(session_id: str, before: int | None = Query(default=None, ge=1, le=2**63 - 1)):
        return await app.state.service.diagnostics(session_id, before=before)

    @app.get('/api/sessions/{session_id}/diagnostics/{run_id}')
    async def diagnostic_events(session_id: str, run_id: str,
                                after: int = Query(default=0, ge=0, le=2**63 - 1)):
        return await app.state.service.diagnostics(session_id, run_id=run_id, after=after)

    @app.get('/api/sessions/{session_id}/subagents/{agent_id}')
    async def subagent(session_id: str, agent_id: str):
        return await app.state.service.subagent(session_id, agent_id)

    @app.patch('/api/sessions/{session_id}/model')
    async def select(session_id: str, body: NewSession):
        return await app.state.service.select_model(session_id, body.model_id)

    @app.patch('/api/sessions/{session_id}/thinking')
    async def thinking(session_id: str, body: ThinkingInput):
        return await app.state.service.select_thinking(session_id, body.model_id, body.thinking_level)

    @app.post('/api/sessions/{session_id}/compact', status_code=202)
    async def compact(session_id: str, body: CompactInput):
        return await app.state.service.compact(session_id, body)

    @app.patch('/api/sessions/{session_id}/tools')
    async def select_tools(session_id: str, body: ActiveToolsInput):
        return await app.state.service.select_tools(session_id, body.active_tools)

    @app.patch('/api/sessions/{session_id}/permission-mode')
    async def select_permission_mode(session_id: str, body: PermissionModeInput):
        return await app.state.service.select_permission_mode(session_id, body.permission_mode)

    @app.post('/api/sessions/{session_id}/runs', status_code=202)
    async def start(session_id: str, body: MessageInput):
        return await app.state.service.start(session_id, body)

    @app.post('/api/sessions/{session_id}/approvals', status_code=202)
    async def approve(session_id: str, body: ApprovalInput):
        return await app.state.service.approve(session_id, body)

    @app.post('/api/sessions/{session_id}/approvals/stop', status_code=202)
    async def stop_approval(session_id: str, body: ApprovalStopInput):
        return await app.state.service.stop_approval(session_id, body)

    @app.post('/api/sessions/{session_id}/recover')
    async def recover(session_id: str, body: RecoveryInput):
        return await app.state.service.recover(session_id, body)

    @app.get('/api/runs/{run_id}')
    async def run(run_id: str):
        return app.state.delivery.get(run_id).view()

    async def stream_job(run_id: str, after: int = Query(default=0, ge=0)):
        job = app.state.delivery.get(run_id)
        job.check_cursor(after)
        return job, after

    @app.get('/api/runs/{run_id}/events', response_class=EventSourceResponse)
    async def events(stream_args=Depends(stream_job)):
        job, after = stream_args
        async for event in job.stream(after):
            yield ServerSentEvent(**event)

    @app.post('/api/runs/{run_id}/controls', status_code=202)
    async def control(run_id: str, body: ControlInput):
        return await app.state.delivery.control(run_id, body)

    # Only built assets are public. Model config and workspace are never mounted.
    app.mount('/', StaticFiles(directory=settings.frontend_dist, html=True, follow_symlink=False))
    return app
