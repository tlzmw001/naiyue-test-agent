"""Streaming response ownership survives disconnect, shutdown and bounded failures."""
import asyncio
import json

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from model_proxy_helpers import model_settings
from test_model_proxy import endpoint,headers
from test_runner_control import prepared


@pytest.mark.parametrize('action',['cancel','shutdown'])
def test_first_chunk_is_forwarded_without_waiting_for_tail_and_cancel_closes_upstream(tmp_path,action):
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            first,tail,closed=asyncio.Event(),asyncio.Event(),asyncio.Event();chunks=[]
            class Stream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield b'data: first\n\n';await tail.wait();yield b'data: tail\n\n'
                async def aclose(self):closed.set()
            relay=r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request:httpx.Response(200,stream=Stream(),headers={'content-type':'text/event-stream'}))))
            _,task=create_cloud_apps(r);body=json.dumps({'model':'fixture-chat','stream':True}).encode()
            scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
                'method':'POST','scheme':'http','path':endpoint(b,'chat','chat/completions'),'raw_path':endpoint(b,'chat','chat/completions').encode(),
                'query_string':b'','headers':[(k.lower().encode(),v.encode()) for k,v in (headers(r,b)|{'content-type':'application/json'}).items()],
                'server':('fixture',80),'client':('fixture',1),'root_path':''}
            async def receive():return {'type':'http.request','body':body,'more_body':False}
            async def send(message):
                if message['type']=='http.response.body' and message.get('body'):
                    chunks.append(message['body']);first.set()
            job=asyncio.create_task(task(scope,receive,send))
            try:
                await asyncio.wait_for(first.wait(),5)
                assert chunks==[b'data: first\n\n'] and not job.done() and len(relay.leases)==1
                if action=='cancel':job.cancel()
                else:await r.models.aclose()
                with pytest.raises(asyncio.CancelledError):await job
                assert closed.is_set() and not relay.leases and chunks==[b'data: first\n\n']
            finally:
                tail.set();job.cancel();await asyncio.gather(job,return_exceptions=True);await r.models.aclose()
        asyncio.run(scenario())


def test_header_retry_semantics_and_bounded_error_body(tmp_path):
    config,_=model_settings(tmp_path,error_bytes=1024)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            calls=0
            def upstream(request):
                nonlocal calls
                calls+=1
                return httpx.Response(429,content=(b'{"error":"fixture"}' if calls==1 else b'x'*2048),
                    headers={'retry-after-ms':'12','x-request-id':'fixture','content-type':'application/json'})
            relay=r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
            _,task=create_cloud_apps(r)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+r.settings.task_listen) as c:
                    response=await c.post(endpoint(b,'chat','chat/completions'),headers=headers(r,b),json={'model':'fixture-chat'})
                    assert response.status_code==429 and response.headers['retry-after-ms']=='12'
                    response=await c.post(endpoint(b,'chat','chat/completions'),headers=headers(r,b),json={'model':'fixture-chat'})
                    assert response.status_code==502 and response.json()['error']['code']=='model_error_too_large'
                    assert calls==2 and not relay.leases
            finally:await r.models.aclose()
        asyncio.run(scenario())


def test_expired_stream_deadline_rejects_already_buffered_bytes(tmp_path):
    import time
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            relay=r.models.relay=ModelRelay(r.models)
            key=next(iter(r.models.routes));route=r.models.routes[key]
            lease=relay.reserve(r.models.admit(b.token.get_secret_value(),b.task_id,key),b.token.get_secret_value())
            lease.response=httpx.Response(200,content=b'already buffered')
            lease.deadline=time.monotonic()-1
            try:
                with pytest.raises(CloudError,match='model_upstream_stream_unconfirmed'):
                    await anext(lease.chunks())
            finally:await r.models.aclose()
        asyncio.run(scenario())


def test_response_deadline_covers_downstream_backpressure(tmp_path):
    import time
    from naiyue_test_agent.service.cloud.model_routes import RelayResponse
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            relay=r.models.relay=ModelRelay(r.models);key=next(iter(r.models.routes))
            lease=relay.reserve(r.models.admit(b.token.get_secret_value(),b.task_id,key),b.token.get_secret_value())
            lease.response=httpx.Response(200,content=b'buffered');lease.deadline=time.monotonic()+0.1
            entered,release=asyncio.Event(),asyncio.Event()
            async def receive():return {'type':'http.disconnect'}
            async def send(message):
                if message['type']=='http.response.body':entered.set();await release.wait()
            job=asyncio.create_task(RelayResponse(lease)({'type':'http','asgi':{'spec_version':'2.4'}},receive,send))
            try:
                await asyncio.wait_for(entered.wait(),5)
                # An outer bound only guards the regression runner; response's
                # own absolute deadline must finish it, without releasing send.
                done,_=await asyncio.wait({job},timeout=2)
                assert job in done,'response ignored its absolute deadline'
                with pytest.raises(TimeoutError):await job
                assert lease.closed and not relay.leases
            finally:
                release.set();job.cancel();await asyncio.gather(job,return_exceptions=True);await r.models.aclose()
        asyncio.run(scenario())


@pytest.mark.parametrize('change,status,calls_expected',[('renew',200,1),('generation',409,0),('stopped',409,0)])
def test_token_rotation_during_admitted_body_preserves_lease_but_generation_and_stop_reject(tmp_path,change,status,calls_expected):
    from types import SimpleNamespace
    from uuid import uuid4
    from naiyue_test_agent.contracts.cloud_runner import Hello
    from test_runner_control import hello
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            calls=[]
            def upstream(request):calls.append(request);return httpx.Response(200,json={'ok':True})
            r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
            _,task=create_cloud_apps(r);entered,release=asyncio.Event(),asyncio.Event();responses=[]
            connection=SimpleNamespace(task_id=b.task_id,raw=b.token.get_secret_value(),connection_id=str(uuid4()))
            r.control.bind(connection,Hello.model_validate(hello(b)))
            body=json.dumps({'model':'fixture-chat'}).encode()
            scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
                'method':'POST','scheme':'http','path':endpoint(b,'chat','chat/completions'),'query_string':b'',
                'headers':[(k.lower().encode(),v.encode()) for k,v in (headers(r,b)|{'content-type':'application/json'}).items()],
                'server':('fixture',80),'client':('fixture',1),'root_path':''}
            async def receive():
                entered.set();await release.wait();return {'type':'http.request','body':body,'more_body':False}
            async def send(message):responses.append(message)
            job=asyncio.create_task(task(scope,receive,send))
            try:
                await asyncio.wait_for(entered.wait(),5)
                if change=='renew':
                    renewed=r.control.bootstrap.renew(connection)
                    assert renewed.token!=b.token
                else:
                    old=r.registry.get_task(b.task_id)
                    value=old.model_copy(update={'revision':old.revision+1,
                        **({'generation':old.generation+1} if change=='generation' else {'lifecycle':'stopped'})})
                    r.replace_task(value,expected_revision=old.revision,expected_generation=old.generation)
                release.set();await asyncio.wait_for(job,5)
                assert responses[0]['status']==status and len(calls)==calls_expected
                assert not r.models.relay.leases
            finally:release.set();job.cancel();await asyncio.gather(job,return_exceptions=True);await r.models.aclose()
        asyncio.run(scenario())


def test_native_error_body_keeps_lease_until_the_downstream_response_finishes(tmp_path):
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            relay=r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request:httpx.Response(429,json={'error':{'message':'fixture'}}))))
            _,task=create_cloud_apps(r);entered,release=asyncio.Event(),asyncio.Event();responses=[]
            body=json.dumps({'model':'fixture-chat'}).encode()
            scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
                'method':'POST','scheme':'http','path':endpoint(b,'chat','chat/completions'),'query_string':b'',
                'headers':[(k.lower().encode(),v.encode()) for k,v in (headers(r,b)|{'content-type':'application/json'}).items()],
                'server':('fixture',80),'client':('fixture',1),'root_path':''}
            async def receive():return {'type':'http.request','body':body,'more_body':False}
            async def send(message):
                responses.append(message)
                if message['type']=='http.response.body' and message.get('body'):entered.set();await release.wait()
            job=asyncio.create_task(task(scope,receive,send))
            try:
                await asyncio.wait_for(entered.wait(),5)
                assert responses[0]['status']==429 and len(relay.leases)==1
                release.set();await asyncio.wait_for(job,5)
                assert not relay.leases
            finally:release.set();job.cancel();await asyncio.gather(job,return_exceptions=True);await r.models.aclose()
        asyncio.run(scenario())
