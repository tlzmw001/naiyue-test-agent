"""Restart history is readable through the same authenticated workspace route."""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel
from test_web_api import web_client, request
from test_web_tools import start
from test_session_history import database_facts


@pytest.mark.parametrize('state', ['running', 'failed', 'interrupted', 'review_required'])
def test_restricted_history_is_readable_without_live_run_or_execution(monkeypatch, tmp_path, state):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='saved answer')])) as (client, app):
            sid, _ = await start(client, 'saved human')
            storage = app.state.service.application._sessions._persistence.storage
            row = storage.get(sid)
            storage.finish_run(sid, state, row['checkpoint_id'], '', state)
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model) as (client, app):
            backend = app.state.service.application
            before = database_facts(backend._sessions._persistence.storage)
            events = []
            backend.subscribe_events(events.append)
            response = await client.get(f'/api/sessions/{sid}')
            assert response.status_code == 200
            view = response.json()
            assert [m['content'] for m in view['messages']] == ['saved human', 'saved answer']
            assert not view['can_run'] and not view['pending_approval'] and view['approval_batches'] == []
            assert view['live'] is None and view['active_run_id'] is None and view['latest_run_id'] is None
            assert view['recovery']['kind'] == 'interrupted_execution' and view['recovery']['actions'] == ['continue']
            assert (await client.post(f'/api/sessions/{sid}/runs', json=request('must not run'))).status_code == 409
            assert (await client.patch(f'/api/sessions/{sid}/model', json={'model_id': 'A'})).status_code == 409
            assert (await client.get(f'/api/sessions/{sid}', headers={'Authorization': ''})).status_code == 401
            assert not backend.has_session(sid) and events == [] and model.seen == []
            assert database_facts(backend._sessions._persistence.storage) == before
            assert app.state.delivery.latest == {}
    asyncio.run(scenario())


def test_ready_checkpoint_mismatch_is_readable_but_not_executable(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='saved')])) as (client, app):
            sid, _ = await start(client)
            storage = app.state.service.application._sessions._persistence.storage
            storage.finish_run(sid, 'ready', 'wrong', '', 'completed')
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, app):
            view = (await client.get(f'/api/sessions/{sid}')).json()
            assert view['messages'][-1]['content'] == 'saved' and not view['can_run']
            assert view['recovery']['reason'] == 'checkpoint_mismatch'
            assert not app.state.service.application.has_session(sid)
    asyncio.run(scenario())


def test_history_stays_workspace_scoped_and_frame_bounded(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'work'
        root.mkdir()
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='x' * 3000)]), workspace_root=root) as (client, app):
            sid, _ = await start(client)
            storage = app.state.service.application._sessions._persistence.storage
            row = storage.get(sid)
            storage.finish_run(sid, 'failed', row['checkpoint_id'], '', 'failed')
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), workspace_root=root, max_frame_bytes=2048) as (client, app):
            response = await client.get(f'/api/sessions/{sid}')
            assert response.status_code == 409 and response.json()['error']['code'] == 'view_too_large'
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, app):
            assert (await client.get(f'/api/sessions/{sid}')).status_code == 404
    asyncio.run(scenario())
