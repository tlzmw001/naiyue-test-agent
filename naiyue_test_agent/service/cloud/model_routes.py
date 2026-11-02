"""Visible task authentication and native relay response lifetime."""
import asyncio
import logging

import httpx
from fastapi import Request
from starlette.responses import StreamingResponse

from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.application.cloud.model_relay import ModelRelay
from naiyue_test_agent.application.cloud.work import cloud_work
from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger=logging.getLogger(__name__)


def model_token(request,runtime,kind):
    if len(request.headers.getlist('host'))!=1 or request.headers.get('host')!=runtime.settings.task_listen:
        raise CloudError('invalid_host',403)
    name='x-api-key' if kind=='anthropic' else 'authorization'
    other='authorization' if name=='x-api-key' else 'x-api-key'
    if len(request.headers.getlist(name))!=1 or request.headers.getlist(other):raise CloudError('unauthorized',401)
    value=request.headers[name]
    if name=='authorization':
        if not value.startswith('Bearer '):raise CloudError('unauthorized',401)
        value=value[7:]
    if request.query_params:raise CloudError('invalid_request',422)
    return value


class RelayResponse(StreamingResponse):
    def __init__(self,lease,*,body=None):
        self.lease=lease
        async def buffered():
            for start in range(0,len(body),lease.relay.config.chunk_bytes):
                yield body[start:start+lease.relay.config.chunk_bytes]
        content=lease.chunks() if body is None else buffered()
        super().__init__(content,status_code=lease.response.status_code,headers=lease.headers())

    async def __call__(self,scope,receive,send):
        primary=None
        try:
            async with asyncio.timeout_at(self.lease.deadline):await super().__call__(scope,receive,send)
        except BaseException as error:
            primary=error;raise
        finally:
            try:await await_settled(self.lease.aclose())
            except BaseException as error:
                logger.error('model_response_close_failed error_type=%s',type(error).__name__)
                if primary is None:raise


def register_model_routes(task,runtime):
    @task.post('/internal/cloud/v1/tasks/{task_id}/models/{model_key}/{native_path:path}')
    async def invoke(request:Request,task_id:str,model_key:str,native_path:str):
        authority=runtime.models
        route=authority.routes.get(model_key)
        raw=model_token(request,runtime,route.kind if route else None)
        grant=await cloud_work(runtime,lambda:authority.admit(raw,task_id,model_key))
        if authority.relay is None:authority.relay=ModelRelay(authority)
        lease=authority.relay.reserve(grant,raw)
        try:
            upstream=await lease.prepare(request,native_path)
            if upstream.status_code>=400:
                body=await lease.error_body()
                return RelayResponse(lease,body=body)
            return RelayResponse(lease)
        except BaseException as error:
            try:await await_settled(lease.aclose())
            except Exception as secondary:logger.error('model_close_secondary_failed error_type=%s',type(secondary).__name__)
            if isinstance(error,(httpx.HTTPError,TimeoutError)):
                logger.warning('model_upstream_unavailable error_type=%s',type(error).__name__)
                raise CloudError('model_upstream_unavailable',502) from None
            raise
