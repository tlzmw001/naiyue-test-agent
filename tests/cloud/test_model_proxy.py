"""Task-only native relay: fixed upstream, auth, bounds and response ownership."""
import asyncio
import json
from hashlib import sha256

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from model_proxy_helpers import model_settings
from test_runner_control import prepared


def endpoint(b,model_id,path):
    return '/internal/cloud/v1/tasks/'+b.task_id+'/models/'+sha256(model_id.encode()).hexdigest()+'/'+path


def headers(r,b,model='chat'):
    return {'Host':r.settings.task_listen,
        ('x-api-key' if model=='anthropic' else 'Authorization'):
            b.token.get_secret_value() if model=='anthropic' else 'Bearer '+b.token.get_secret_value()}


@pytest.mark.parametrize('model,path,upstream_path',[
    ('chat','chat/completions','/v1/chat/completions'),('responses','responses','/v1/responses'),
    ('anthropic','v1/messages','/v1/messages'),('ollama','api/chat','/api/chat')])
def test_native_relay_preserves_body_and_replaces_auth_without_forwarding_unsafe_headers(tmp_path,model,path,upstream_path):
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            seen=[];content=b'data: first\n\ndata: second\n\n'
            def upstream(request):
                seen.append(request)
                return httpx.Response(200,content=content,headers={'content-type':'text/event-stream',
                    'set-cookie':'secret=value','authorization':'bad','x-request-id':'fixture-id'})
            relay=r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
            _,task=create_cloud_apps(r)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+r.settings.task_listen) as c:
                    body=dict(model='fixture-'+model,messages=[],stream=True)
                    response=await c.post(endpoint(b,model,path),json=body,headers=headers(r,b,model)|{
                        'x-inject-upstream':'http://bad.invalid','anthropic-version':'2023-06-01','cookie':'owner=bad'})
                    assert response.status_code==200 and response.content==content
                    assert response.headers['x-request-id']=='fixture-id'
                    assert 'set-cookie' not in response.headers and 'authorization' not in response.headers
                    assert len(seen)==1 and seen[0].url.path==upstream_path and json.loads(seen[0].content)==body
                    assert seen[0].url.host=='127.0.0.1' and 'cookie' not in seen[0].headers and 'x-inject-upstream' not in seen[0].headers
                    if model=='anthropic':
                        assert seen[0].headers['x-api-key']=='fixture-upstream-anthropic'
                        assert 'authorization' not in seen[0].headers and seen[0].headers['anthropic-version']=='2023-06-01'
                    elif model=='ollama':assert 'authorization' not in seen[0].headers
                    else:assert seen[0].headers['authorization']=='Bearer fixture-upstream-openai'
                    assert b.token.get_secret_value() not in str(seen[0].headers)
                    assert not relay.leases
            finally:await r.models.aclose()
        asyncio.run(scenario())


@pytest.mark.parametrize('failure,status',[
    ('no_scope',401),('expired',401),('other_task',401),('profile',409),('stopped',409),('generation',401),('wrong_model',403),
    ('path',403),('query',422),('duplicate_auth',401),('wrong_auth_kind',401),('encoding',415),
    ('duplicate_json',422),('background',422),('host',403),('size',413)])
def test_relay_rejections_make_zero_upstream_calls(tmp_path,failure,status):
    config,_=model_settings(tmp_path,max_request_bytes=1024)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            seen=[]
            def upstream(request):seen.append(request);return httpx.Response(200,json={'ok':True})
            r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
            _,task=create_cloud_apps(r);path=endpoint(b,'chat','chat/completions');auth=headers(r,b)
            body=json.dumps(dict(model='fixture-chat',messages=[])).encode()
            if failure=='no_scope':auth['Authorization']='Bearer '+r.credentials.issue(b.task_id,scopes=['identity:read']).token.get_secret_value()
            if failure=='expired':
                import time
                with r.database.transaction(write=True) as c:c.execute('UPDATE task_credentials SET expires_at=? WHERE task_id=?',(time.time()-1,b.task_id))
            if failure=='other_task':path=path.replace(b.task_id,prepared(r).task_id)
            if failure in ('profile','stopped'):
                old=r.registry.get_task(b.task_id)
                value=old.model_copy(update={'revision':old.revision+1,**({'model_profile_id':'f'*64} if failure=='profile' else {'lifecycle':'stopped'})})
                r.replace_task(value,expected_revision=old.revision,expected_generation=old.generation)
            if failure=='generation':
                old=r.registry.get_task(b.task_id)
                r.replace_task(old.model_copy(update={'revision':old.revision+1,'generation':old.generation+1}),
                    expected_revision=old.revision,expected_generation=old.generation)
            if failure=='wrong_model':body=b'{"model":"other","messages":[]}'
            if failure=='path':path=endpoint(b,'chat','models')
            if failure=='query':path+='?upstream=http://bad.invalid'
            if failure=='duplicate_auth':auth=list(auth.items())+[('authorization',auth['Authorization'])]
            if failure=='wrong_auth_kind':auth={'Host':r.settings.task_listen,'x-api-key':b.token.get_secret_value()}
            if failure=='encoding':auth['content-encoding']='gzip'
            if failure=='duplicate_json':body=b'{"model":"fixture-chat","model":"other"}'
            if failure=='background':body=b'{"model":"fixture-chat","background":true}'
            if failure=='host':auth['Host']=r.settings.owner_listen
            if failure=='size':body=b'{"model":"fixture-chat","input":"'+b'x'*1200+b'"}'
            auth=list(auth.items()) if isinstance(auth,dict) else auth
            auth.append(('content-type','application/json'))
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+r.settings.task_listen) as c:
                    response=await c.post(path,content=body,headers=auth)
                    assert response.status_code==status,response.text
                assert seen==[] and not r.models.relay.leases
            finally:await r.models.aclose()
        asyncio.run(scenario())


def test_native_error_status_retry_header_and_redaction_and_redirect_rejection(tmp_path):
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            calls=[]
            def upstream(request):
                calls.append(request)
                if len(calls)==1:return httpx.Response(429,json={'error':{'message':'fixture-upstream-openai '+b.token.get_secret_value(),'type':'rate_limit'}},headers={'retry-after':'2','x-should-retry':'true'})
                return httpx.Response(307,headers={'location':'https://bad.invalid/leak'})
            relay=r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
            _,task=create_cloud_apps(r)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+r.settings.task_listen) as c:
                    path=endpoint(b,'chat','chat/completions');body=dict(model='fixture-chat')
                    response=await c.post(path,json=body,headers=headers(r,b))
                    assert response.status_code==429 and response.headers['retry-after']=='2'
                    assert response.json()['error']['type']=='rate_limit'
                    assert 'fixture-upstream-openai' not in response.text and b.token.get_secret_value() not in response.text
                    response=await c.post(path,json=body,headers=headers(r,b))
                    assert response.status_code==502 and 'location' not in response.headers
                    assert len(calls)==2 and not relay.leases
            finally:await r.models.aclose()
        asyncio.run(scenario())


def test_capacity_is_reserved_before_upstream_wait_and_released_after_completion(tmp_path):
    config,_=model_settings(tmp_path,per_task=1,total=1)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            entered,release=asyncio.Event(),asyncio.Event();calls=0
            async def upstream(request):
                nonlocal calls
                calls+=1;entered.set();await release.wait();return httpx.Response(200,json={'ok':True})
            relay=r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
            _,task=create_cloud_apps(r)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+r.settings.task_listen) as c:
                    path=endpoint(b,'chat','chat/completions');kwargs=dict(json={'model':'fixture-chat'},headers=headers(r,b))
                    first=asyncio.create_task(c.post(path,**kwargs));await asyncio.wait_for(entered.wait(),5)
                    second=await c.post(path,**kwargs)
                    assert second.status_code==429 and calls==1
                    release.set();assert (await first).status_code==200
                    assert (await c.post(path,**kwargs)).status_code==200 and calls==2 and not relay.leases
            finally:release.set();await r.models.aclose()
        asyncio.run(scenario())


def test_shared_upstream_client_never_replays_cookie_between_task_or_provider_requests(tmp_path):
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        first=prepared(r);second=prepared(r)
        async def scenario():
            from naiyue_test_agent.application.cloud.model_relay import ModelRelay
            seen=[]
            def upstream(request):
                seen.append(request)
                return httpx.Response(200,json={'ok':True},headers={'set-cookie':'session=fixture-private; Path=/'})
            r.models.relay=ModelRelay(r.models,client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
            _,task=create_cloud_apps(r)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+r.settings.task_listen) as c:
                    for b,model,path in [(first,'chat','chat/completions'),(second,'anthropic','v1/messages')]:
                        response=await c.post(endpoint(b,model,path),headers=headers(r,b,model),json={'model':'fixture-'+model})
                        assert response.status_code==200
                    assert len(seen)==2 and all('cookie' not in request.headers for request in seen)
            finally:await r.models.aclose()
        asyncio.run(scenario())
