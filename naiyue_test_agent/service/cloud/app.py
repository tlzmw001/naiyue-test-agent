"""Two disjoint apps sharing one externally-owned CloudRuntime."""
import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.responses import JSONResponse

from naiyue_test_agent import __version__
from naiyue_test_agent.contracts.cloud_events import EventBatch
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CreateProject
from naiyue_test_agent.contracts.cloud_capabilities import owner_capabilities
from naiyue_test_agent.contracts.cloud_versions import OWNER_PROTOCOL
from .auth import owner_authorize, task_bearer
from .http import _body
from naiyue_test_agent.application.cloud.work import cloud_work

logger = logging.getLogger(__name__)



def _app():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(CloudError)
    async def known_error(request, error):
        value = {'code': error.code}
        if error.last_seq is not None: value['last_seq'] = error.last_seq
        return JSONResponse({'error': value}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid(request, error):
        return JSONResponse({'error': {'code': 'invalid_request'}}, status_code=422)

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def storage_error(request, error):
        logger.error('cloud_request_storage_failed error_type=%s', type(error).__name__)
        return JSONResponse({'error': {'code': 'cloud_storage_unavailable'}}, status_code=503)

    return app


def create_cloud_apps(runtime):
    owner, task = _app(), _app()

    @owner.get('/api/cloud/v1/instance')
    async def instance(request: Request):
        owner_authorize(request, runtime)
        return dict(instance_id=runtime.instance_id, protocol=OWNER_PROTOCOL, version=__version__,
            capabilities=owner_capabilities(runtime.lifecycle is not None))

    @owner.get('/api/cloud/v1/projects')
    async def projects(request: Request):
        owner_authorize(request, runtime)
        return await cloud_work(runtime, runtime.registry.list_projects)

    @owner.get('/api/cloud/v1/capacity')
    async def capacity(request: Request):
        owner_authorize(request, runtime)
        return await cloud_work(runtime, runtime.resources.capacity)

    @owner.post('/api/cloud/v1/projects')
    async def create_project(request: Request):
        owner_authorize(request, runtime)
        command = CreateProject.model_validate(await _body(request, runtime))
        receipt = await cloud_work(runtime, lambda: runtime.commands.create_project(command))
        return JSONResponse({'project': receipt.result['project'], 'receipt': receipt.model_dump()},
            status_code=receipt.http_status)

    @owner.get('/api/cloud/v1/commands/{command_id}')
    async def command(request: Request, command_id: str):
        owner_authorize(request, runtime)
        return await cloud_work(runtime, lambda: runtime.registry.get_command(command_id))

    @owner.get('/api/cloud/v1/tasks/{task_id}')
    async def metadata(request: Request, task_id: str):
        owner_authorize(request, runtime)
        return await cloud_work(runtime, lambda: runtime.registry.get_task(task_id))

    @owner.get('/api/cloud/v1/tasks/{task_id}/history/events')
    async def history(request: Request, task_id: str, epoch: str | None = None, after_seq: int = 0, limit: int | None = None):
        owner_authorize(request, runtime, query_fields=('epoch', 'after_seq', 'limit'))
        return await cloud_work(runtime, lambda: runtime.history_page(task_id, epoch=epoch, after_seq=after_seq, limit=limit))

    @task.get('/internal/cloud/v1/tasks/{task_id}/identity')
    async def identity(request: Request, task_id: str):
        raw = task_bearer(request, runtime)
        return await cloud_work(runtime, lambda: runtime.identity(raw, task_id))

    @task.post('/internal/cloud/v1/tasks/{task_id}/events')
    async def events(request: Request, task_id: str):
        raw = task_bearer(request, runtime)
        # Authenticate before reading the body or allocating task/history state.
        await cloud_work(runtime, lambda: runtime.credentials.authorize(raw, task_id, 'events:append'))
        batch = EventBatch.model_validate(await _body(request, runtime))
        if batch.task_id != task_id:
            raise CloudError('task_identity_mismatch', 403)
        ack=await cloud_work(runtime, lambda: runtime.append_events(raw, batch.model_dump()))
        try:runtime.observations.changed(task_id,archive=ack)
        except (CloudError,ValueError,RuntimeError) as error:
            logger.warning('cloud_live_archive_notification_failed error_type=%s',type(error).__name__)
        return ack

    if runtime.lifecycle is not None:
        @owner.post('/api/cloud/v1/tasks/{task_id}/commands')
        async def environment_command(request: Request, task_id: str):
            owner_authorize(request, runtime)
            if runtime.lifecycle is None: raise CloudError('cloud_backend_not_configured', 503)
            receipt = await runtime.lifecycle.command(task_id, await _body(request, runtime))
            return JSONResponse(receipt.model_dump(), status_code=receipt.http_status)

        @owner.post('/api/cloud/v1/commands/{command_id}/reconcile')
        async def reconcile_command(request: Request, command_id: str):
            owner_authorize(request, runtime)
            if runtime.lifecycle is None: raise CloudError('cloud_backend_not_configured', 503)
            if await _body(request, runtime) != {}: raise CloudError('invalid_request', 422)
            current = await cloud_work(runtime, lambda: runtime.registry.get_command(command_id))
            if current.operation=='release:upgrade':raise CloudError('command_not_environment',422)
            service = runtime.deletions if current.operation == 'task:delete' else runtime.lifecycle
            receipt = await service.reconcile(command_id)
            return JSONResponse(receipt.model_dump(), status_code=receipt.http_status)

        from .delete_routes import register_delete_routes
        register_delete_routes(owner, runtime)

    from .input_routes import register_input_routes
    register_input_routes(owner, runtime)
    from .runner_routes import register_runner_routes
    register_runner_routes(owner,task,runtime)
    from .artifact_routes import register_artifact_routes
    register_artifact_routes(owner,task,runtime)
    from .result_routes import register_result_routes
    register_result_routes(owner,task,runtime)
    from .model_routes import register_model_routes
    register_model_routes(task,runtime)
    from .history_routes import register_history_routes
    register_history_routes(owner,runtime)
    from .observation_routes import register_observation_routes
    register_observation_routes(owner,runtime)
    from .release_routes import register_release_routes
    register_release_routes(owner,runtime)
    return owner, task
