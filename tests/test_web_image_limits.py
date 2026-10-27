"""Image request admission rejects overload before decoding and keeps readers responsive."""
import asyncio
from threading import Event,Lock
from uuid import uuid4
from langchain_core.messages import AIMessage
from test_async_application import StreamingToolModel
from test_composer_image_inputs import upload
from test_web_api import web_client


def test_image_workers_are_bounded_while_readonly_requests_continue(monkeypatch,tmp_path):
    from naiyue_test_agent.application.conversation import image_inputs
    original=image_inputs.normalize_input
    entered,release,guard=Event(),Event(),Lock()
    count=0
    def normalize(*args):
        nonlocal count
        with guard:
            count+=1
            if count==2:entered.set()
        if not release.wait(10):raise TimeoutError('Image test worker was not released')
        return original(*args)
    monkeypatch.setattr(image_inputs,'normalize_input',normalize)
    async def scenario():
        model=StreamingToolModel(replies=[AIMessage(content='one'),AIMessage(content='two')],profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model) as (client,app):
            sessions=[(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id'] for _ in range(2)]
            async def send(sid):
                return await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'images':[upload()]},headers={'X-Image-Input':'1'})
            tasks=[asyncio.create_task(send(sid)) for sid in sessions]
            try:
                assert await asyncio.to_thread(entered.wait,5)
                assert app.state.receipts.images.active==2
                response=await send(sessions[0])
                assert response.status_code==429 and response.json()['error']['code']=='image_input_busy'
                assert (await client.get('/api/v1/scopes/chats/models')).status_code==200
            finally:
                release.set()
                responses=await asyncio.gather(*tasks)
            assert all(response.status_code==202 for response in responses)
            assert app.state.receipts.images.active==0
            for response in responses:
                await client.get(f"/api/v1/scopes/chats/runs/{response.json()['run_id']}/events")
    asyncio.run(scenario())


def test_auth_and_route_checks_precede_large_body_and_oversize_releases_slot(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[],profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model) as (client,app):
            from naiyue_test_agent.image_input_settings import ImageInputSettings
            from naiyue_test_agent.service.image_requests import ImageRequests
            app.state.receipts.images=ImageRequests(ImageInputSettings(max_input_bytes=1024,max_total_input_bytes=2048,max_request_bytes=150000))
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            reads=[]
            async def body():
                reads.append(True)
                yield b'x'*(app.state.receipts.images.settings.max_request_bytes+1)
            headers={'X-Image-Input':'1','X-Request-ID':str(uuid4())}
            # The shared fixture's JSON request-ID hook eagerly inspects .content;
            # streaming transport must retain receive() ownership for this check.
            import httpx
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=client.base_url,headers=client.headers) as streaming:
                response=await streaming.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',content=body(),headers=headers|{'Authorization':''})
                assert response.status_code==401 and reads==[]
                response=await streaming.post('/api/v1/scopes/chats/sessions',content=body(),headers=headers)
                assert response.status_code==422 and reads==[]
                response=await streaming.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',content=body(),headers=headers)
                assert response.status_code==413 and reads==[True]
                assert app.state.receipts.images.active==0 and model.seen==[]

    asyncio.run(scenario())


def test_stop_does_not_wait_for_image_normalization(monkeypatch,tmp_path):
    from naiyue_test_agent.application.conversation import image_inputs
    original=image_inputs.normalize_input
    entered,release=Event(),Event()
    def normalize(*args):
        entered.set()
        if not release.wait(10):raise TimeoutError('image worker was not released')
        return original(*args)
    monkeypatch.setattr(image_inputs,'normalize_input',normalize)
    async def scenario():
        model_entered,model_release=asyncio.Event(),asyncio.Event()
        model=StreamingToolModel(replies=[],entered=model_entered,release=model_release,profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model) as (client,app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            run=(await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'text':'start'})).json()['run_id']
            await asyncio.wait_for(model_entered.wait(),5)
            image=asyncio.create_task(client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'follow_up','text':'','images':[upload()]},headers={'X-Image-Input':'1'}))
            stop=None
            try:
                assert await asyncio.to_thread(entered.wait,5)
                stop=asyncio.create_task(client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'stop'}))
                result=await asyncio.wait_for(asyncio.shield(stop),2)
                assert result.status_code==202 and not release.is_set()
            finally:
                release.set()
                response=await image
                if stop is not None:await stop
            assert response.status_code==409
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs')).json()['inputs']==[]
    asyncio.run(scenario())
