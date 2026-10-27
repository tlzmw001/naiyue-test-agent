"""Real authenticated HTTP ingress, canonical pixels and safe public errors."""
import asyncio
from uuid import uuid4
from langchain_core.messages import AIMessage
from test_web_api import web_client
from test_async_application import StreamingToolModel
from test_composer_image_inputs import upload
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.image_input_settings import ImageInputSettings


def test_image_only_request_and_capability_limits(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='seen')], profile={'image_inputs': True})
        runtime = AppSettings(image_inputs=ImageInputSettings(max_dimension=20))
        async with web_client(monkeypatch,tmp_path,model,runtime_settings=runtime) as (client, app):
            models = (await client.get('/api/v1/scopes/chats/models')).json()
            assert models['capabilities']['A']['image_inputs'] is True
            assert models['image_input_limits']['max_dimension'] == 20
            sid = (await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            payload = {'request_id':str(uuid4()),'images':[upload()]}
            response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json=payload,headers={'X-Image-Input':'1'})
            assert response.status_code == 202, response.text
            run = response.json()['run_id']
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            human = next(m for m in model.seen[0] if m.type == 'human')
            image = next(b for b in human.content if b['type'] == 'image')
            assert image['extras']['width'] == 20
            assert human.additional_kwargs['naiyue_image_origin_run_id'] == run
            assert (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json=payload,headers={'X-Image-Input':'1'})).json()['run_id'] == run
            assert len(model.seen) == 1
    asyncio.run(scenario())


def test_unsupported_images_are_rejected_without_model_call(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[AIMessage(content='unused')])
        async with web_client(monkeypatch,tmp_path,model) as (client, app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            response=await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'images':[upload()]},headers={'X-Image-Input':'1'})
            assert response.status_code==422 and response.json()['error']['code']=='model_image_input_unsupported'
            assert not model.seen
    asyncio.run(scenario())


def test_image_body_requires_budget_header(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[],profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model) as (client, app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            response=await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'images':[upload()]})
            assert response.status_code==422 and response.json()['error']['code']=='image_request_header_required'
    asyncio.run(scenario())


def test_saved_image_metadata_and_authenticated_bytes(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[AIMessage(content='ok')],profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model) as (client,app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            payload={'request_id':str(uuid4()),'text':'look','images':[upload()]}
            response=await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json=payload,headers={'X-Image-Input':'1'})
            run=response.json()['run_id']
            events=await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            assert upload()['base64'] not in events.text
            view=(await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
            human=next(m for m in view['messages'] if m['type']=='human')
            image=human['images'][0]
            assert image['name']=='shot.png' and image['width']==64 and image['height']==32
            path=f"/api/v1/scopes/chats/sessions/{sid}/messages/{human['id']}/images/{image['index']}"
            params={'source_revision':image['source_revision'],'conversation_revision':view['conversation_revision']}
            result=await client.get(path,params=params)
            assert result.status_code==200, result.text
            assert result.headers['cache-control']=='no-store' and result.content.startswith(b'\x89PNG')
            assert (await client.get(path,params=params,headers={'Authorization':''})).status_code==401
            assert (await client.get(path,params=params|{'source_revision':'0'*64})).status_code==409
            missing=await client.get(path.replace(sid,'missing-session'),params=params)
            assert missing.status_code==404 and missing.json()['error']['code']=='image_not_found'
    asyncio.run(scenario())
