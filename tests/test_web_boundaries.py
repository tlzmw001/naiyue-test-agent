"""HTTP failure and restore contracts reuse application checks, not UI guesses."""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from test_web_api import web_client, request
from test_async_application import StreamingToolModel


def test_completed_session_restores_after_web_lifespan_restart(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='first')])) as (client, app):
            session = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{session}/runs', json=request('one'))).json()['run_id']
            await client.get(f'/api/runs/{run}/events')
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='second')])) as (client, app):
            assert (await client.post(f'/api/sessions/{session}/prepare')).status_code == 200
            old = (await client.get(f'/api/sessions/{session}')).json()
            assert [m['content'] for m in old['messages']] == ['one', 'first']
            run = (await client.post(f'/api/sessions/{session}/runs', json=request('two'))).json()['run_id']
            await client.get(f'/api/runs/{run}/events')
            assert [m['content'] for m in (await client.get(f'/api/sessions/{session}')).json()['messages']] == [
                'one', 'first', 'two', 'second']
            assert [m['content'] for m in (await client.get(f'/api/runs/{run}')).json()['messages']] == ['two', 'second']
    asyncio.run(scenario())


def test_model_failure_is_safe_and_restart_does_not_replay(monkeypatch, tmp_path, caplog):
    async def scenario():
        model = StreamingToolModel(replies=[RuntimeError('PRIVATE_GATEWAY_BODY')])
        async with web_client(monkeypatch, tmp_path, model) as (client, app):
            session = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{session}/runs', json=request('start'))).json()['run_id']
            stream = await client.get(f'/api/runs/{run}/events')
            view = (await client.get(f'/api/runs/{run}')).json()
            assert view['status'] == 'failed' and 'PRIVATE_GATEWAY_BODY' not in stream.text
        next_model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, next_model) as (client, app):
            assert (await client.post(f'/api/sessions/{session}/prepare')).status_code == 200
            response = await client.get(f'/api/sessions/{session}')
            assert response.status_code == 200 and next_model.seen == []
            history = response.json()
            assert history['recovery']['kind'] == 'interrupted_execution' and not history['can_run']
            assert [m['content'] for m in history['messages']] == ['start']
            assert history['live'] is None and history['recovery']['actions'] == ['continue']
            assert 'PRIVATE_GATEWAY_BODY' not in response.text
            assert (await client.post(f'/api/sessions/{session}/runs', json=request('no replay'))).status_code == 409
            assert next_model.seen == [] and not app.state.service.application.has_session(session)
    asyncio.run(scenario())
    assert 'PRIVATE_GATEWAY_BODY' not in caplog.text


def test_public_assets_do_not_expose_workspace_or_config(monkeypatch, tmp_path):
    async def scenario():
        secret = tmp_path / 'secret.txt'
        secret.write_text('PRIVATE_FILE')
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, app):
            response = await client.get('/')
            assert response.headers['cache-control'] == 'no-store'
            assert "frame-ancestors 'none'" in response.headers['content-security-policy']
            assert response.headers['referrer-policy'] == 'no-referrer'
            for path in ('/../secret.txt', '/.env/models.json', '/requirements.txt'):
                result = await client.get(path)
                assert result.status_code == 404 and 'PRIVATE_FILE' not in result.text
    asyncio.run(scenario())


def test_request_window_is_bounded_without_eviction_replays(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='answer')])
        async with web_client(monkeypatch, tmp_path, model, max_receipts=1) as (client, app):
            session = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            body = request('first')
            first = await client.post(f'/api/sessions/{session}/runs', json=body)
            await client.get(f'/api/runs/{first.json()["run_id"]}/events')
            blocked = await client.post(f'/api/sessions/{session}/runs', json=request('next'))
            assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'request_window_full'
            duplicate = await client.post(f'/api/sessions/{session}/runs', json=body)
            assert duplicate.json() == first.json() and len(model.seen) == 1
    asyncio.run(scenario())


def test_save_failure_cannot_be_presented_as_web_completion(monkeypatch, tmp_path):
    from naiyue_test_agent.application.errors import SessionPersistenceError

    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='answer')])) as (client, app):
            session = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            def fail(*args):
                raise SessionPersistenceError('PRIVATE_SAVE_BODY')
            monkeypatch.setattr(app.state.service.application._storage.runs, 'finish_run', fail)
            run = (await client.post(f'/api/sessions/{session}/runs', json=request('one'))).json()['run_id']
            stream = await client.get(f'/api/runs/{run}/events')
            view = (await client.get(f'/api/runs/{run}')).json()
            assert view['status'] == 'failed' and view['done'] is True
            assert 'PRIVATE_SAVE_BODY' not in stream.text
            assert (await client.post(f'/api/sessions/{session}/runs', json=request('retry'))).status_code == 409
    asyncio.run(scenario())
