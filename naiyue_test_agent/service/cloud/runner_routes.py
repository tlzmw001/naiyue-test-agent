"""Disjoint owner admission and authenticated task-only WebSocket boundary."""
import asyncio
import logging

from fastapi import Request,WebSocket
from pydantic import ValidationError
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.contracts.cloud_runner import RunnerFrame
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.cloud.work import cloud_work
from .auth import owner_authorize,task_bearer
from .http import _body

logger=logging.getLogger(__name__)


def register_runner_routes(owner,task,runtime):
    @owner.get('/api/cloud/v1/tasks/{task_id}/models')
    async def models(request:Request,task_id:str):
        owner_authorize(request,runtime)
        return await cloud_work(runtime,lambda:runtime.models.options(task_id))

    @owner.get('/api/cloud/v1/tasks/{task_id}/mcp')
    async def mcp_status(request: Request, task_id: str):
        owner_authorize(request, runtime)
        return await cloud_work(runtime, lambda: runtime.mcp.status(task_id))

    @owner.post('/api/cloud/v1/tasks/{task_id}/mcp')
    async def mcp_refresh(request: Request, task_id: str):
        owner_authorize(request, runtime)
        return await runtime.mcp.refresh(task_id, await _body(request, runtime))

    @owner.post('/api/cloud/v1/tasks/{task_id}/runs')
    async def start(request:Request,task_id:str):
        owner_authorize(request,runtime)
        receipt=await runtime.control.runs.start(task_id,await _body(request,runtime))
        return JSONResponse(receipt.model_dump(),status_code=receipt.http_status)

    @owner.post('/api/cloud/v1/tasks/{task_id}/control')
    async def command(request:Request,task_id:str):
        owner_authorize(request,runtime)
        receipt=await runtime.control.actions.command(task_id,await _body(request,runtime))
        return JSONResponse(receipt.model_dump(),status_code=receipt.http_status)

    @task.websocket('/internal/cloud/v1/tasks/{task_id}/control')
    async def control(websocket:WebSocket,task_id:str):
        connection=None
        async def frame():
            async with asyncio.timeout(runtime.settings.control_receive_timeout_seconds):
                raw=await websocket.receive_text()
            return RunnerFrame.validate_python(parse_object(raw,byte_limit=runtime.settings.max_frame_bytes,
                depth_limit=runtime.settings.json_depth_limit))
        try:
            raw=task_bearer(websocket,runtime)
            await cloud_work(runtime,lambda:runtime.credentials.authorize(raw,task_id,'control:connect'))
            await websocket.accept()
            hello=await frame()
            if hello.kind!='hello' or hello.task_id!=task_id:raise CloudError('runner_identity_mismatch')
            connection=await runtime.control.attach(websocket,raw,hello)
            while True:
                value=await frame()
                if value.kind=='hello':raise CloudError('invalid_request')
                await runtime.control.receive(connection,value)
        except WebSocketDisconnect:
            logger.info('cloud_runner_disconnected')
        except (CloudError,ValidationError,TimeoutError,KeyError) as error:
            logger.warning('cloud_runner_control_rejected code=%s',error.code if isinstance(error,CloudError) else 'invalid_request')
            if connection is not None:await runtime.control.detach(connection)
            await websocket.close(code=1008,reason=error.code if isinstance(error,CloudError) else 'invalid_request')
        finally:
            if connection is not None:await runtime.control.detach(connection)
