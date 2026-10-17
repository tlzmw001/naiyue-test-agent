"""Local HTTP boundary; the lifespan owns one existing async application."""

from contextlib import asynccontextmanager
import logging

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.staticfiles import StaticFiles

from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.tools import create_builtin_tools, load_tool_settings
from .auth import authorize
from .errors import WebError
from .run_delivery import RunDelivery
from .schemas import ActiveToolsInput, ApprovalInput, ApprovalStopInput, CompactInput, ControlInput, MessageInput, NewSession, PermissionModeInput, RecoveryInput, SessionOrder, SessionPresentation, ThinkingInput
from .service import WebService
from .headers import LocalHeaders


logger = logging.getLogger(__name__)


def create_web_app(settings, *, application_factory=None):
    @asynccontextmanager
    async def lifespan(app):
        tool_settings = settings.runtime_settings if settings.runtime_settings is not None else load_tool_settings()
        tools = create_builtin_tools(tool_settings)
        initial_tools = [spec.tool.name for spec in tools if spec.tool.name not in ('bash', 'powershell')] if settings.workspace_root is not None else []
        factory = application_factory or (lambda: open_async_persistent_application(
            settings.config_path, database_path=settings.database_path, tools=tools,
            system_prompt=BASE_SYSTEM_PROMPT, response_format=None, initial_active_tools=initial_tools, settings=tool_settings,
            workspace_binding={'workspace_id': settings.workspace_id, 'root_path': str(settings.workspace_root)}
                if settings.workspace_id is not None else None))
        async with factory() as application:
            delivery = RunDelivery(application, settings, progress_settings=tool_settings.progress)
            app.state.delivery = delivery
            app.state.service = WebService(application, delivery, settings)
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

    @app.get('/api/session-controls')
    async def session_control_options():
        return app.state.service.session_control_options()

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

    @app.get('/api/sessions/{session_id}/subagents')
    async def subagents(session_id: str):
        return await app.state.service.subagents(session_id)

    @app.get('/api/sessions/{session_id}/context')
    async def context(session_id: str):
        return await app.state.service.context(session_id)

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
