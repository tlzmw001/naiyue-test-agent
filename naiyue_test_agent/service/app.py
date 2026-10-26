"""One service lock and Application own every explicitly addressed HTTP scope."""
from contextlib import asynccontextmanager
import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.application.sessions.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.config_paths import StorageSettings
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.settings import load_settings
from naiyue_test_agent.storage.errors import SessionPersistenceError, StorageStartupError
from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.execution_roots import ExecutionRootUnavailable
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.web.auth import authorize
from naiyue_test_agent.web.errors import WebError, public_error
from naiyue_test_agent.web.headers import LocalHeaders
from naiyue_test_agent.web.routes import create_api_router
from naiyue_test_agent.web.settings import WebSettings
from naiyue_test_agent.web.windows_disconnect import windows_disconnect_handler
from .browser import BrowserBoundary, BrowserFiles
from .ownership import ServiceOwner
from .projects import router as project_router
from .navigation import router as navigation_router
from .directories import router as directory_router
from .receipts import CommandReceipts, IdempotentCommands
from .scopes import ScopeServices
from .settings import ServiceError, ServiceIdentity, StartupFailure
from .temporary import TemporaryScopes, router as temporary_router
from .terminal import router as terminal_router
from .terminal_sessions import router as terminal_session_router
from .legacy_history import router as legacy_history_router

logger = logging.getLogger(__name__)


def create_service_app(options, *, application_factory=None, shutdown=None):
    @asynccontextmanager
    async def lifespan(app):
        try:
            with ServiceOwner(options) as owner, windows_disconnect_handler():
                runtime = load_settings(options.settings_path).model_copy(
                    update={'storage': StorageSettings(data_dir=options.data_dir)})
                settings = WebSettings(config_path=options.config_path,
                    database_path=options.data_dir / 'chats/sessions.sqlite',
                    frontend_dist=options.frontend_dist, runtime_settings=runtime,
                    host=options.host, port=options.port, access_token=owner.record.access_token,
                    max_frame_bytes=options.max_frame_bytes, max_receipts=options.max_receipts,
                    event_cache_bytes=runtime.web_event_cache_bytes)
                if application_factory is None:
                    tools = create_builtin_tools(runtime.tools, process_settings=runtime.process,
                        shell_task_settings=runtime.shell_tasks, progress_settings=runtime.progress,
                        network_settings=runtime.network, retry_settings=runtime.tool_retry)
                    initial = runtime.default_active_tools
                    if initial is None:
                        initial = [spec.tool.name for spec in tools if spec.tool.name not in ('bash', 'powershell', 'subagent_list')]
                    factory = lambda: open_service_application(options.config_path,
                        tools=tools, system_prompt=BASE_SYSTEM_PROMPT,
                        response_format=None, initial_active_tools=initial, settings=runtime)
                else:
                    factory = application_factory
                async with factory() as application:
                    scopes = ScopeServices(application, settings, settings_path=options.settings_path)
                    receipts = CommandReceipts(options)
                    scopes.temporary = TemporaryScopes(scopes, receipts)
                    app.state.temporary = scopes.temporary
                    app.state.application, app.state.scopes = application, scopes
                    app.state.receipts, app.state.web_settings = receipts, settings
                    app.state.owner = owner
                    owner.publish()
                    primary = None
                    try:
                        yield
                    except BaseException as error:
                        primary = error
                        raise
                    finally:
                        try:
                            await scopes.shutdown(receipts)
                        except BaseException as error:
                            if primary is None:
                                primary = error
                            else:
                                logger.error('service_cleanup_secondary_failed error_type=%s', type(error).__name__)
                        if primary is not None:
                            raise primary
        except BaseException as error:
            logger.error('service_lifecycle_failed error_type=%s', type(error).__name__)
            raise

    async def access(request: Request):
        authorize(request, app.state.web_settings)
        if app.state.scopes.closing:
            raise WebError('service_closing', 503)

    app = FastAPI(lifespan=lifespan, dependencies=[Depends(access)],
        docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(IdempotentCommands, owner=app)
    app.add_middleware(BrowserBoundary, owner=app)
    app.add_middleware(LocalHeaders)

    @app.exception_handler(WebError)
    async def web_error(request, error):
        return JSONResponse({'error': {'code': error.code,
            **({'reason': error.reason} if error.reason else {})}}, status_code=error.status)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse({'error': {'code': error.code}}, status_code=409)

    @app.exception_handler(StorageStartupError)
    async def storage_failure(request, error):
        facts = StartupFailure(database_path=str(error.database_path), phase=error.phase,
            target_schema=error.target_schema, source_schema=error.source_schema,
            current_schema=error.current_schema, backup_id=error.backup_id)
        return JSONResponse({'error': {'code': str(error), 'startup': facts.model_dump()}}, status_code=409)

    @app.exception_handler(ProjectError)
    async def project_error(request, error):
        status = 404 if error.code in ('project_not_found', 'project_not_open') else 409
        logger.info('service_project_rejected code=%s', error.code)
        return JSONResponse({'error': {'code': error.code}}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        logger.warning('service_request_invalid')
        return JSONResponse({'error': {'code': 'invalid_request'}}, status_code=422)

    @app.exception_handler(Exception)
    @app.exception_handler(SessionPersistenceError)
    @app.exception_handler(ModelConfigError)
    @app.exception_handler(ExecutionRootUnavailable)
    @app.exception_handler(SandboxError)
    @app.exception_handler(GitInputError)
    async def failure(request, error):
        logger.error('service_request_failed error_type=%s', type(error).__name__)
        return await web_error(request, public_error(error))

    @app.post('/api/v1/handshake')
    async def handshake(body: ServiceIdentity):
        options.identity.check(body)
        return {'identity': options.identity.model_dump(mode='json'),
                'instance_id': app.state.owner.record.instance_id}

    @app.post('/api/v1/shutdown')
    async def stop_service():
        if shutdown is None:
            raise WebError('service_shutdown_unavailable', 503)
        shutdown()
        return {'accepted': True}

    app.include_router(create_api_router(), prefix='/api/v1/scopes/{scope_id}')
    app.include_router(terminal_session_router, prefix='/api/v1/scopes/{scope_id}')
    app.include_router(project_router)
    app.include_router(navigation_router)
    app.include_router(directory_router)
    app.include_router(temporary_router)
    app.include_router(terminal_router)
    app.include_router(legacy_history_router)
    app.mount('/', BrowserFiles(directory=options.frontend_dist, html=True, follow_symlink=False, check_dir=False))
    return app
