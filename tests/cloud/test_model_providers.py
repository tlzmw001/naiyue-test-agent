"""Real native SDKs through real HTTP task relay; no provider credentials or traffic."""
import asyncio
import json

import pytest
import httpx
from fastapi import FastAPI,Request
from starlette.responses import Response

from naiyue_test_agent.cloud_runner.kernel import open_runner
from http_observation_helpers import sdk_response
from model_http_helpers import live_app,cloud_task
from model_proxy_helpers import model_settings
from test_runner_control import prepared


@pytest.mark.parametrize('model',['chat','responses','anthropic','ollama'])
@pytest.mark.parametrize('stream',[False,True])
def test_native_sdk_uses_task_relay_with_existing_configuration_and_stream_semantics(tmp_path,model,stream):
    async def scenario():
        upstream=FastAPI();seen=[]
        @upstream.post('/{path:path}')
        async def serve(request:Request,path:str):
            raw=await request.body();seen.append((path,dict(request.headers),json.loads(raw)))
            class Input:content=raw
            response=sdk_response(Input(),'openai' if model=='chat' else model)
            return Response(response.content,status_code=response.status_code,headers=dict(response.headers))
        async with live_app(upstream) as url:
            config,_=model_settings(tmp_path,upstream=url)
            async with cloud_task(config) as (r,owner,task_url):
                b=prepared(r)
                root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
                async with open_runner(b,root,state) as kernel:
                    assert kernel.phase=='ready' and kernel.catalog is not None
                    native=kernel.catalog.get(model)
                    result=[c async for c in native.astream('fixture input')] if stream else await native.ainvoke('fixture input')
                    assert result and len(seen)==1
                    assert seen[0][2]['model']=='fixture-'+model
                    expected=('x-api-key' if model=='anthropic' else 'authorization')
                    if model!='ollama':assert seen[0][1][expected].endswith('fixture-upstream-'+('anthropic' if model=='anthropic' else 'openai'))
                    assert b.token.get_secret_value() not in json.dumps(seen)
                    assert kernel.remote_models is not None
    asyncio.run(scenario())


@pytest.mark.parametrize('model',['chat','responses','anthropic'])
def test_sdk_retries_remain_sdk_owned_and_proxy_sends_exactly_one_upstream_request_per_attempt(tmp_path,model):
    async def scenario():
        upstream=FastAPI();seen=[]
        @upstream.post('/{path:path}')
        async def serve(request:Request,path:str):
            raw=await request.body();seen.append(path)
            if len(seen)<3:return Response('{"error":{"message":"fixture unavailable"}}',status_code=503,
                headers={'retry-after':'0.001','content-type':'application/json'})
            class Input:content=raw
            response=sdk_response(Input(),'openai' if model=='chat' else model)
            return Response(response.content,status_code=response.status_code,headers=dict(response.headers))
        async with live_app(upstream) as url:
            config,data=model_settings(tmp_path,upstream=url)
            data['models'][model]['request']['sdk_max_retries']=2
            config.model_proxy.config_path.write_text(json.dumps(data))
            async with cloud_task(config) as (r,owner,task_url):
                b=prepared(r);root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
                async with open_runner(b,root,state) as kernel:
                    result=await kernel.catalog.get(model).ainvoke('retry fixture')
                    assert result and len(seen)==3
                    assert not r.models.relay.leases
    asyncio.run(scenario())


def test_openai_compatible_adapter_keeps_kind_and_fixed_https_upstream_with_internal_task_http(tmp_path):
    from naiyue_test_agent.application.cloud.model_relay import ModelRelay
    config,data=model_settings(tmp_path)
    data['providers']['o']['kind']='openai_compatible'
    data['providers']['o']['base_url']='https://fixture.example.invalid/v1'
    config.model_proxy.config_path.write_text(json.dumps(data))
    config=config.model_copy(update={'model_proxy':config.model_proxy.model_copy(update={
        'upstreams':{'a':'http://127.0.0.1:29991'}})})
    async def scenario():
        async with cloud_task(config) as (r,_,_url):
            seen=[]
            def upstream(request):seen.append(request);return sdk_response(request,'openai')
            r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
            b=prepared(r);root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
            async with open_runner(b,root,state) as kernel:
                model=kernel.catalog.get('chat');result=await model.ainvoke('compatible fixture')
                assert result.content=='done' and len(seen)==1
                assert str(seen[0].url)=='https://fixture.example.invalid/v1/chat/completions'
                assert model.metadata['naiyue_model_input']['kind']=='openai_compatible'
    asyncio.run(scenario())
