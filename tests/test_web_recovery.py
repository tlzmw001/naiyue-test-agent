"""Recovery is explicit, authorized, workspace-scoped and never a run."""

import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel
from test_web_api import web_client, request
from test_web_tools import start, calls, approval_request
from test_session_history import database_facts


def test_recover_then_wait_for_new_input_and_reject_duplicate_checkpoint(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[RuntimeError('PRIVATE_MODEL')])) as (client, app):
            sid, _ = await start(client)
        model = StreamingToolModel(replies=[AIMessage(content='new answer')])
        async with web_client(monkeypatch, tmp_path, model) as (client, app):
            backend = app.state.service.application
            events = []
            backend.subscribe_events(events.append)
            endpoint = f'/api/sessions/{sid}/recover'
            view = (await client.get(f'/api/sessions/{sid}')).json()
            assert view['recovery']['actions'] == ['continue']
            body = {'checkpoint_id': view['checkpoint_id'], 'action': 'continue'}
            assert (await client.post(endpoint, json=body, headers={'Authorization': ''})).status_code == 401
            assert (await client.post(endpoint, json=body, headers={'Origin': 'https://invalid.example'})).status_code == 403
            for invalid in ({**body, 'checkpoint_id': 'old'},):
                assert (await client.post(endpoint, json=invalid)).status_code == 409
            for invalid in ({**body, 'action': 'invalid'}, {**body, 'run_id': 'invented'}):
                assert (await client.post(endpoint, json=invalid)).status_code == 422
            assert (await client.post(endpoint, json={**body, 'action': 'restore_approval'})).status_code == 409
            before = database_facts(backend._sessions._persistence.storage)
            assert (await client.post(endpoint, json={**body, 'checkpoint_id': 'old'})).status_code == 409
            assert database_facts(backend._sessions._persistence.storage) == before
            response = await client.post(endpoint, json=body)
            assert response.status_code == 200
            ready = response.json()
            assert ready['can_run'] and ready['state'] == 'ready' and 'recovery' not in ready
            assert ready['live'] is None and ready['latest_run_id'] is None and ready['active_run_id'] is None
            assert [m['content'] for m in ready['messages']] == ['use tools']
            assert model.seen == [] and events == [] and app.state.delivery.runs == {}
            assert (await client.post(endpoint, json=body)).status_code == 409
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('new input'))).json()['run_id']
            await client.get(f'/api/runs/{run}/events')
            assert len(model.seen) == 1
            assert (await client.get(f'/api/sessions/{sid}')).json()['messages'][-1]['content'] == 'new answer'
    asyncio.run(scenario())


def test_recovery_and_start_share_admission_lock_and_preserve_draft_on_failure(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[RuntimeError('fixture')])) as (client, app):
            sid, _ = await start(client)
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, app):
            endpoint = f'/api/sessions/{sid}/recover'
            history = (await client.get(f'/api/sessions/{sid}')).json()
            entered, release = asyncio.Event(), asyncio.Event()
            original = app.state.service.application.arecover_session
            async def pause(*args, **kwargs):
                assert app.state.delivery.start_lock.locked()
                entered.set()
                await release.wait()
                return await original(*args, **kwargs)
            monkeypatch.setattr(app.state.service.application, 'arecover_session', pause)
            task = asyncio.create_task(client.post(endpoint, json={'checkpoint_id': history['checkpoint_id'], 'action': 'continue'}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert (await client.get(f'/api/sessions/{sid}')).json()['can_run'] is False
                release.set()
                assert (await task).status_code == 200
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_continue_abandons_saved_approval_without_running_tools(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'work'
        root.mkdir()
        outside = tmp_path / 'outside'
        outside.write_text('must not be read')
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[calls(('read', {'path': str(outside)}, 'a'))]), workspace_root=root) as (client, app):
            sid, origin = await start(client)
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            history = (await client.get(f'/api/sessions/{sid}')).json()
            assert history['recovery']['kind'] == 'pending_approval'
            result = await client.post(f'/api/sessions/{sid}/recover', json={'checkpoint_id': history['checkpoint_id'], 'action': 'continue'})
            assert result.status_code == 200
            ready = result.json()
            assert ready['can_run'] and not ready['pending_approval'] and ready['approval_batches'] == []
            assert 'not executed' in ready['messages'][-1]['content'] and model.seen == []
        async with web_client(monkeypatch, tmp_path, model) as (client, app):
            assert (await client.post(f'/api/sessions/{sid}/recover', json={'checkpoint_id': history['checkpoint_id'], 'action': 'continue'})).status_code == 404
    asyncio.run(scenario())


def test_cold_approval_restores_cards_and_requires_a_fresh_user_decision(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'work'
        root.mkdir()
        outside = tmp_path / 'outside'
        outside.write_text('actual approved content')
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[calls(('read', {'path': str(outside)}, 'a'))]), workspace_root=root) as (client, app):
            sid, origin = await start(client)
        model = StreamingToolModel(replies=[AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            backend = app.state.service.application
            events = []
            backend.subscribe_events(events.append)
            before = database_facts(backend._sessions._persistence.storage)
            history = (await client.get(f'/api/sessions/{sid}')).json()
            assert history['recovery']['actions'] == ['restore_approval', 'continue']
            assert database_facts(backend._sessions._persistence.storage) == before
            body = {'checkpoint_id': history['checkpoint_id'], 'action': 'restore_approval'}
            endpoint = f'/api/sessions/{sid}/recover'
            assert (await client.post(endpoint, json=body, headers={'Authorization': ''})).status_code == 401
            assert (await client.post(endpoint, json=body, headers={'Origin': 'https://invalid.example'})).status_code == 403
            assert (await client.post(endpoint, json={**body, 'checkpoint_id': 'old'})).status_code == 409
            restored = await client.post(endpoint, json=body)
            assert restored.status_code == 200
            pending = restored.json()
            assert pending['state'] == 'interrupted' and pending['pending_approval'] and not pending['can_run']
            batch, = pending['approval_batches']
            assert batch['origin_run_id'] == origin
            assert batch['items'] == [{'approval_id': batch['items'][0]['approval_id'], 'tool_call_id': 'a', 'tool_name': 'read', 'target': str(outside)}]
            assert not any(m['type'] == 'tool' for m in pending['messages'])
            assert events == [] and model.seen == [] and not app.state.delivery.runs
            assert (await client.post(endpoint, json=body)).status_code == 409
            assert (await client.post(f'/api/sessions/{sid}/runs', json=request('premature'))).status_code == 409
            decision = approval_request(batch, ['approve'])
            response = await client.post(f'/api/sessions/{sid}/approvals', json=decision)
            assert response.status_code == 202
            run = response.json()['run_id']
            assert run != origin
            await client.get(f'/api/runs/{run}/events')
            ready = (await client.get(f'/api/sessions/{sid}')).json()
            assert ready['can_run'] and not ready['pending_approval']
            assert [m['content'] for m in ready['messages'] if m['type'] == 'tool'] == ['actual approved content']
            assert len(model.seen) == 1
    asyncio.run(scenario())
