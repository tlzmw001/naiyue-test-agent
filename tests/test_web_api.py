"""HTTP adapts real async application state, never manufactures a run outcome."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.image_input_settings import ImageInputSettings
from test_async_application import StreamingToolModel
from scripts.web_probe_app import create_probe_app
from naiyue_test_agent.web.settings import WebSettings


ORIGIN = 'http://127.0.0.1:8123'
HEADERS = {'Authorization': 'Bearer unit-test-access', 'Origin': ORIGIN}


def request(text):
    return {'request_id': str(uuid4()), 'text': text}


from scripts.web_probe_app import async_command_headers as command_headers

@asynccontextmanager
async def web_client(monkeypatch, tmp_path, model, **options):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    assets = tmp_path / 'assets'
    assets.mkdir(exist_ok=True)
    (assets / 'index.html').write_text('<html><body>test fixture</body></html>')
    settings = WebSettings(config_path='unused', database_path=tmp_path / 'sessions.sqlite',
        frontend_dist=assets, port=8123, access_token='unit-test-access', **options)
    app = create_probe_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                                    headers=HEADERS, event_hooks={'request': [command_headers]}) as client:
            yield client, app


def test_web_models_auth_and_validation_do_not_leak_data(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model) as (client, app):
            assert (await client.get('/api/v1/scopes/chats/models', headers={'Authorization': ''})).status_code == 401
            assert (await client.get('/api/v1/scopes/chats/models', headers={'Host': 'evil.example'})).status_code == 403
            response = await client.get('/api/v1/scopes/chats/models')
            assert response.json() == {'default_model_id': 'A', 'model_ids': ['A'],
                'thinking_options': {'A': {'supported_levels': [], 'default_level': 'model_default'}},
                'capabilities': {'A': {'image_inputs': False}},
                'image_input_limits': ImageInputSettings().model_dump()}
            assert (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'},
                                     headers={'Origin': 'https://evil.example'})).status_code == 403
            malformed = await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A', 'api_key': 'PRIVATE_VALUE'})
            assert malformed.status_code == 422 and 'PRIVATE_VALUE' not in malformed.text
            assert model.seen == []
    asyncio.run(scenario())


def test_web_start_stream_and_idempotency_preserve_one_run(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='answer')])
        async with web_client(monkeypatch, tmp_path, model) as (client, app):
            session = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            body = request('question')
            first = await client.post(f'/api/v1/scopes/chats/sessions/{session}/runs', json=body)
            assert first.status_code == 202
            run = first.json()['run_id']
            stream = await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            assert stream.status_code == 200 and 'text/event-stream' in stream.headers['content-type']
            assert 'run_started' in stream.text and 'message_delta' in stream.text and 'run_finished' in stream.text
            repeat = await client.post(f'/api/v1/scopes/chats/sessions/{session}/runs', json=body)
            assert repeat.json() == first.json() and len(model.seen) == 1
            conflict = await client.post(f'/api/v1/scopes/chats/sessions/{session}/runs', json={**body, 'text': 'different'})
            assert conflict.status_code == 409
            view = (await client.get(f'/api/v1/scopes/chats/sessions/{session}')).json()
            assert [m['content'] for m in view['messages']] == ['question', 'answer']
            assert view['busy'] is False
            run_view = (await client.get(f'/api/v1/scopes/chats/runs/{run}')).json()
            assert run_view['status'] == 'completed' and run_view['last_sequence'] > 0
    asyncio.run(scenario())


def test_web_stop_works_while_model_waits_and_preserves_pending(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[], entered=entered, release=release)
        async with web_client(monkeypatch, tmp_path, model) as (client, app):
            session = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/v1/scopes/chats/sessions/{session}/runs', json=request('start'))).json()['run_id']
            await asyncio.wait_for(entered.wait(), 5)
            assert (await client.post(f'/api/v1/scopes/chats/sessions/{session}/runs', json=request('overlap'))).status_code == 409
            control = {'request_id': str(uuid4()), 'kind': 'steering', 'text': 'retained'}
            assert (await client.post(f'/api/v1/scopes/chats/runs/{run}/controls', json=control)).status_code == 202
            assert (await client.post(f'/api/v1/scopes/chats/runs/{run}/controls', json=control)).status_code == 202
            stopping = await client.post(f'/api/v1/scopes/chats/runs/{run}/controls', json={
                'request_id': str(uuid4()), 'kind': 'stop'})
            assert stopping.json()['retained'] == ['retained']
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            view = (await client.get(f'/api/v1/scopes/chats/runs/{run}')).json()
            assert view['status'] == 'stopped'
            assert view['retained'] == ['retained']
            assert not any(row['type'] == 'ai' for row in view['messages'])
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{session}')).json()['busy'] is False
    asyncio.run(scenario())


def test_web_delivery_overflow_is_explicit_and_does_not_stop_run(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='answer')])
        async with web_client(monkeypatch, tmp_path, model, event_capacity=2) as (client, app):
            session = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/v1/scopes/chats/sessions/{session}/runs', json=request('start'))).json()['run_id']
            await asyncio.wait_for(app.state.delivery.wait(run), 5)
            missing = await client.get(f'/api/v1/scopes/chats/runs/{run}/events?after=0')
            assert missing.status_code == 409 and missing.json()['error']['code'] == 'resync_required'
            view = (await client.get(f'/api/v1/scopes/chats/runs/{run}')).json()
            assert view['status'] == 'completed' and view['messages'][-1]['content'] == 'answer'
            synced = await client.get(f'/api/v1/scopes/chats/runs/{run}/events?after={view["last_sequence"]}')
            assert synced.status_code == 200
    asyncio.run(scenario())
