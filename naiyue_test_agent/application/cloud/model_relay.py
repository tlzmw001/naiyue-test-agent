"""Single-attempt native HTTP relay; leases own upstream responses and capacity."""
import asyncio
import json
import logging
import time

import httpx

from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .work import cloud_work

logger=logging.getLogger(__name__)


class ModelRelay:
    def __init__(self,authority,*,client=None):
        self.authority=authority;self.config=authority.config
        self.client=client;self.leases=set();self.closing=False

    def reserve(self,grant,raw):
        if self.closing:raise CloudError('model_proxy_closing',503)
        if len(self.leases)>=self.config.total or sum(l.task_id==grant.task_id for l in self.leases)>=self.config.per_task:
            raise CloudError('model_capacity_exceeded',429)
        lease=ModelLease(self,grant,raw);self.leases.add(lease)
        return lease

    def http(self):
        if self.client is None:
            self.client=httpx.AsyncClient(follow_redirects=False,timeout=None)
        return self.client

    async def aclose(self):
        self.closing=True
        primary=None
        try:await self._close_leases(tuple(self.leases))
        except BaseException as error:primary=error
        if self.client is not None:
            try:await self.client.aclose()
            except BaseException as error:
                logger.error('model_client_close_failed error_type=%s',type(error).__name__)
                primary=primary or error
        if primary is not None:raise primary

    async def close_task(self,task_id):
        await self._close_leases(tuple(l for l in self.leases if l.task_id==task_id))

    async def _close_leases(self,leases):
        current=asyncio.current_task()
        owners={l.owner for l in leases if l.owner is not current and not l.owner.done()}
        for job in owners:job.cancel()
        if owners:await asyncio.gather(*owners,return_exceptions=True)
        primary=None
        for lease in leases:
            try:await lease.aclose()
            except BaseException as error:
                logger.error('model_response_close_failed error_type=%s',type(error).__name__)
                primary=primary or error
        if primary is not None:raise primary


class ModelLease:
    def __init__(self,relay,grant,raw):
        self.relay,self.grant,self.raw=relay,grant,raw
        self.task_id,self.route=grant.task_id,grant.route
        self.response=None;self.closed=False;self.owner=asyncio.current_task()
        self.deadline=time.monotonic()+relay.config.max_duration_seconds

    def remaining(self):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('model_request_deadline')
        return remaining

    async def prepare(self,request,path):
        config=self.relay.config;route=self.route
        if path!=route.path:raise CloudError('model_endpoint_not_allowed',403)
        for name in ('content-type','content-encoding','content-length'):
            if len(request.headers.getlist(name))>1:raise CloudError('invalid_headers',400)
        if (request.headers.get('content-encoding') is not None
                or request.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json'):
            raise CloudError('invalid_model_content_type',415)
        raw=bytearray()
        async with asyncio.timeout(min(config.body_timeout_seconds,self.remaining())):
            async for chunk in request.stream():
                if len(raw)+len(chunk)>config.max_request_bytes:raise CloudError('model_request_too_large',413)
                raw.extend(chunk)
        value=parse_object(bytes(raw),byte_limit=config.max_request_bytes,depth_limit=64)
        if value.get('model')!=route.model_name:raise CloudError('model_not_allowed',403)
        if value.get('background') is True or ('stream' in value and type(value['stream']) is not bool):
            raise CloudError('invalid_model_request',422)
        await cloud_work(self.relay.authority.runtime,
            lambda:self.relay.authority.check(self.grant))
        headers={'content-type':'application/json','accept-encoding':'identity'}
        if route.kind=='anthropic':
            headers['x-api-key']=route.api_key
            for name in ('anthropic-version','anthropic-beta'):
                if len(request.headers.getlist(name))>1:raise CloudError('invalid_headers',400)
                if name in request.headers:headers[name]=request.headers[name]
        elif route.api_key is not None:headers['authorization']='Bearer '+route.api_key
        client=self.relay.http()
        outgoing=client.build_request('POST',route.upstream+'/'+route.path,content=bytes(raw),headers=headers,
            timeout=httpx.Timeout(route.timeout))
        # The shared pool must not reuse a provider's Set-Cookie as another
        # task/provider credential. Mutate only this final request, not the jar.
        outgoing.headers.pop('cookie',None)
        async with asyncio.timeout(self.remaining()):self.response=await client.send(outgoing,stream=True)
        if 300<=self.response.status_code<400:raise CloudError('model_upstream_redirect_rejected',502)
        if self.response.headers.get('content-encoding','identity').lower()!='identity':
            raise CloudError('model_upstream_encoding_rejected',502)
        return self.response

    def headers(self):
        allowed={'content-type','request-id','x-request-id','retry-after','retry-after-ms','x-should-retry'}
        return {k:v for k,v in self.response.headers.items() if k in allowed or k.startswith(('x-ratelimit-','anthropic-ratelimit-'))}

    async def chunks(self):
        size=0;iterator=self.response.aiter_bytes().__aiter__()
        try:
            while True:
                try:
                    async with asyncio.timeout(self.remaining()):chunk=await anext(iterator)
                except StopAsyncIteration:break
                size+=len(chunk)
                if size>self.relay.config.max_response_bytes:raise CloudError('model_response_too_large',502)
                for start in range(0,len(chunk),self.relay.config.chunk_bytes):
                    yield chunk[start:start+self.relay.config.chunk_bytes]
        except (httpx.HTTPError,TimeoutError) as error:
            logger.warning('model_upstream_stream_unconfirmed error_type=%s',type(error).__name__)
            raise CloudError('model_upstream_stream_unconfirmed',502) from None

    async def error_body(self):
        raw=bytearray()
        async for chunk in self.chunks():
            if len(raw)+len(chunk)>self.relay.config.error_bytes:raise CloudError('model_error_too_large',502)
            raw.extend(chunk)
        value=bytes(raw)
        for secret in (self.route.api_key,self.raw):
            if secret:
                for spelling in (secret.encode(),json.dumps(secret)[1:-1].encode()):
                    value=value.replace(spelling,b'[redacted]')
        return value

    async def aclose(self):
        if self.closed:return
        try:
            if self.response is not None:await await_settled(self.response.aclose())
        finally:
            self.closed=True;self.relay.leases.discard(self)
