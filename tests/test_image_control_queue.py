import asyncio
from uuid import uuid4
from langchain_core.messages import AIMessage
from test_async_application import StreamingToolModel
from test_composer_image_inputs import upload
from test_web_api import web_client


def test_stopped_image_followup_survives_run_and_is_readable(monkeypatch,tmp_path):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        model=StreamingToolModel(replies=[AIMessage(content='ok')],entered=entered,release=release,profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model) as (client,app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            run=(await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'text':'start'})).json()['run_id']
            await asyncio.wait_for(entered.wait(),5)
            payload={'request_id':str(uuid4()),'kind':'follow_up','text':'','images':[upload()]}
            response=await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json=payload,headers={'X-Image-Input':'1'})
            assert response.status_code==202,response.text
            await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'stop'})
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            pending=(await client.get(f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs')).json()['inputs']
            assert len(pending)==1 and pending[0]['status']=='retained' and pending[0]['images'][0]['name']=='shot.png'
            assert upload()['base64'] not in str(pending)
            identity=pending[0]['message_id']; index=pending[0]['images'][0]['index']
            image=await client.get(f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs/{identity}/images/{index}')
            assert image.status_code==200 and image.content.startswith(b'\x89PNG')
            response=await client.request('DELETE',f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs/{identity}',json={'request_id':str(uuid4())})
            assert response.status_code==200
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs')).json()['inputs']==[]
    asyncio.run(scenario())


def test_followup_delivers_pixels_and_releases_saved_custody(monkeypatch,tmp_path):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        model=StreamingToolModel(replies=[AIMessage(content='first'),AIMessage(content='image seen')],entered=entered,release=release,profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model) as (client,app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            run=(await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'text':'start'})).json()['run_id']
            await asyncio.wait_for(entered.wait(),5)
            response=await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'follow_up','images':[upload()]},headers={'X-Image-Input':'1'})
            assert response.status_code==202
            release.set(); await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            assert len(model.seen)==2
            assert any(m.type=='human' and isinstance(m.content,list) and any(b.get('type')=='image' for b in m.content) for m in model.seen[1])
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs')).json()['inputs']==[]
    asyncio.run(scenario())


def test_pending_image_limit_is_per_session_and_stop_preserves_original(monkeypatch,tmp_path):
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        model=StreamingToolModel(replies=[],entered=entered,release=release,profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model,runtime_settings=AppSettings(image_inputs=ImageInputSettings(max_pending_image_messages=1))) as (client,app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            run=(await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'text':'start'})).json()['run_id']
            await asyncio.wait_for(entered.wait(),5)
            async def enqueue():
                return await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'steering','images':[upload()]},headers={'X-Image-Input':'1'})
            assert (await enqueue()).status_code==202
            response=await enqueue()
            assert response.status_code==409 and response.json()['error']['code']=='pending_image_limit'
            response=await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'stop'})
            assert response.json()['retained']==[]  # pixels cannot be re-sent as legacy text
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            pending=(await client.get(f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs')).json()['inputs']
            assert len(pending)==1 and pending[0]['status']=='retained'
    asyncio.run(scenario())


def test_selected_image_with_unconfirmed_delivery_is_not_automatically_replayed(monkeypatch,tmp_path):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        model=StreamingToolModel(replies=[AIMessage(content='one'),AIMessage(content='two')],entered=entered,release=release,profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model) as (client,app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            run=(await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'text':'start'})).json()['run_id']
            await asyncio.wait_for(entered.wait(),5)
            controls=app.state.service.application.runs._sessions.require(sid).controls
            original=controls.prepare_control_messages
            def fail(messages):raise RuntimeError('synthetic injection failure before message state')
            monkeypatch.setattr(controls,'prepare_control_messages',fail)
            response=await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'follow_up','images':[upload()]},headers={'X-Image-Input':'1'})
            assert response.status_code==202
            release.set();await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            pending=(await client.get(f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs')).json()['inputs']
            assert len(pending)==1 and pending[0]['status']=='uncertain' and len(model.seen)==1
            monkeypatch.setattr(controls,'prepare_control_messages',original)
            # A read never invokes a model or retries the selected input.
            await client.get(f'/api/v1/scopes/chats/sessions/{sid}')
            assert len(model.seen)==1
            data=await client.get(f"/api/v1/scopes/chats/sessions/{sid}/pending-inputs/{pending[0]['message_id']}/images/{pending[0]['images'][0]['index']}")
            assert data.status_code==200
    asyncio.run(scenario())


def test_long_pending_text_uses_bounded_metadata_and_lossless_text_chunks(monkeypatch,tmp_path):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        model=StreamingToolModel(replies=[],entered=entered,release=release,profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model,max_frame_bytes=4096) as (client,app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            run=(await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'text':'start'})).json()['run_id']
            await asyncio.wait_for(entered.wait(),5)
            text='保留文字'*8192
            response=await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'follow_up','text':text,'images':[upload()]},headers={'X-Image-Input':'1'})
            assert response.status_code==202,response.text
            response=await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'stop'})
            assert response.status_code==202,response.text
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            response=await client.get(f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs')
            assert len(response.content)<=4096
            item=response.json()['inputs'][0]
            assert item['text_truncated'] is True and 'legacy_text' not in item
            offset=0;parts=[]
            while True:
                chunk=(await client.get(f"/api/v1/scopes/chats/sessions/{sid}/pending-inputs/{item['message_id']}/text",params={'offset':offset})).json()
                parts.append(chunk['text'])
                assert chunk['next_offset']>offset or chunk['complete']
                offset=chunk['next_offset']
                if chunk['complete']:break
            assert ''.join(parts)==text
    asyncio.run(scenario())


def test_nondefault_pending_byte_budget_rejects_without_partial_queue(monkeypatch,tmp_path):
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        model=StreamingToolModel(replies=[],entered=entered,release=release,profile={'image_inputs':True})
        async with web_client(monkeypatch,tmp_path,model,runtime_settings=AppSettings(image_inputs=ImageInputSettings(max_pending_image_bytes=1))) as (client,app):
            sid=(await client.post('/api/v1/scopes/chats/sessions',json={'model_id':'A'})).json()['session_id']
            run=(await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs',json={'request_id':str(uuid4()),'text':'start'})).json()['run_id']
            await asyncio.wait_for(entered.wait(),5)
            response=await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'steering','images':[upload()]},headers={'X-Image-Input':'1'})
            assert response.status_code==409 and response.json()['error']['code']=='pending_image_limit'
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/pending-inputs')).json()['inputs']==[]
            await client.post(f'/api/v1/scopes/chats/runs/{run}/controls',json={'request_id':str(uuid4()),'kind':'stop'})
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
    asyncio.run(scenario())
