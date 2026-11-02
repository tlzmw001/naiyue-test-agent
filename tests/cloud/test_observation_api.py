"""Public HTTP/SSE proof and streaming response lifetime without real providers."""
import asyncio
import json

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import owner_headers
from lifecycle_helpers import config
from observation_helpers import running,item,frame


def test_snapshot_usage_auth_and_stream_cursor_are_real_routes(tmp_path):
    async def run(r):
        b,c,cmd=await running(r);owner,_=create_cloud_apps(r)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin,headers=owner_headers(r)) as client:
            base='/api/cloud/v1/tasks/'+b.task_id
            snapshot=await client.get(base+'/snapshot');assert snapshot.status_code==200,snapshot.text
            assert snapshot.json()['task']['active_run_id']==cmd['run_id']
            assert (await client.get(base+'/usage')).status_code==200
            assert (await client.get(base+'/stream',headers={'Last-Event-ID':'forged'})).status_code==422
            for endpoint in ('snapshot','usage','stream'):
                assert (await client.get(base+'/'+endpoint,headers={'Authorization':'Bearer '+b.token.get_secret_value()})).status_code==401
                assert (await client.get(base+'/'+endpoint,params={'token':'not-a-query-credential'})).status_code==422
            capabilities=(await client.get('/api/cloud/v1/instance')).json()['capabilities']
            assert {'observations:snapshot','observations:stream','usage:read'}<=set(capabilities)
        await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))


@pytest.mark.parametrize('mode',['disconnect','slow_send','shutdown'])
def test_sse_first_snapshot_and_response_cleanup(tmp_path,mode):
    async def run(r):
        b,c,cmd=await running(r);owner,_=create_cloud_apps(r)
        base='/api/cloud/v1/tasks/'+b.task_id+'/stream'
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1','method':'GET','scheme':'http',
            'path':base,'raw_path':base.encode(),'query_string':b'',
            'headers':[(k.lower().encode(),v.encode()) for k,v in (owner_headers(r)|{'Host':r.settings.authority}).items()],
            'server':('fixture',80),'client':('fixture',1),'root_path':''}
        first=asyncio.Event();blocked=asyncio.Event();chunks=[]
        async def receive():await blocked.wait();return {'type':'http.disconnect'}
        async def send(message):
            if message['type']=='http.response.body' and message.get('body'):
                chunks.append(message['body']);first.set()
                if mode=='slow_send':await blocked.wait()
        job=asyncio.create_task(owner(scope,receive,send))
        try:
            await asyncio.wait_for(first.wait(),5)
            assert b'event: snapshot\n' in chunks[0] and len(r.observations.leases)==1
            if mode=='slow_send':
                from naiyue_test_agent.service.cloud.observation_routes import ObservationStreamTimeout
                with pytest.raises(ObservationStreamTimeout,match='observation_send_timeout') as caught:await job
                assert isinstance(caught.value.__cause__,TimeoutError)
            elif mode=='disconnect':
                job.cancel()
                with pytest.raises(asyncio.CancelledError):await job
            else:await r.observations.aclose();await asyncio.wait_for(job,5)
            assert not r.observations.leases
        finally:
            blocked.set();job.cancel();await asyncio.gather(job,return_exceptions=True)
            await r.observations.aclose();await r.control.aclose()
    from naiyue_test_agent.contracts.cloud_observation import ObservationLimits
    with open_cloud_runtime(config(tmp_path,observation_limits=ObservationLimits(send_timeout_seconds=0.05))) as r:
        asyncio.run(run(r))
