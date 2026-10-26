"""Cancellation, per-host cache lifetime, and root shutdown ownership."""
import asyncio
from uuid import uuid4

from langchain_core.messages import AIMessage
import pytest

from naiyue_test_agent.service.ownership import service_running
from test_async_application import StreamingToolModel
from test_multi_project_runtime import projects
from test_service_protocol import command, service_clients


def test_cancelled_request_does_not_cancel_accepted_session_creation(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, b, app, _):
            service = await app.state.scopes.get('chats')
            original = service.create_session
            entered, release = asyncio.Event(), asyncio.Event()
            async def create(model_id):
                entered.set()
                await release.wait()
                return await original(model_id)
            monkeypatch.setattr(service, 'create_session', create)
            headers = command()
            url = '/api/v1/scopes/chats/sessions'
            pending = asyncio.create_task(a.post(url, json={'model_id': 'A'}, headers=headers))
            await asyncio.wait_for(entered.wait(), 5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            release.set()
            result = await b.post(url, json={'model_id': 'A'}, headers=headers)
            assert result.status_code == 201
            assert len((await b.get(url)).json()) == 1
    asyncio.run(scenario())


def test_project_close_reopen_and_relocate_replace_cached_host(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, b, app, _):
            (pa, _), _ = await projects(app.state.application, tmp_path)
            base = f'/api/v1/scopes/{pa.project_id}'
            sid = (await a.post(base + '/sessions', json={'model_id': 'A'}, headers=command())).json()['session_id']
            first = app.state.scopes.services[pa.project_id]
            assert (await a.post(f'/api/v1/projects/{pa.project_id}/close', headers=command())).status_code == 200
            assert pa.project_id not in app.state.scopes.services
            root = tmp_path / 'moved'
            (tmp_path / 'a').rename(root)
            moved = await b.post(f'/api/v1/projects/{pa.project_id}/relocate', headers=command(),
                json={'expected_root': str(tmp_path / 'a'), 'root': str(root), 'trusted': True})
            assert moved.status_code == 200
            view = await b.get(base + f'/sessions/{sid}')
            assert view.status_code == 200
            assert view.json()['execution_root_error'] is None
            assert view.json()['workspace_root'] == str(root) and view.json()['last_execution_path'] == str(tmp_path / 'a')
            assert (await a.get(base + '/service')).json()['workspace_root'] == str(root)
            assert app.state.scopes.services[pa.project_id] is not first
    asyncio.run(scenario())


def test_one_client_disconnects_while_run_and_other_client_continue(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='answer')], entered=entered, release=release)
        async with service_clients(monkeypatch, tmp_path, model) as (a, b, app, config):
            base = '/api/v1/scopes/chats'
            sid = (await a.post(base + '/sessions', json={'model_id': 'A'}, headers=command())).json()['session_id']
            body = {'request_id': str(uuid4()), 'text': 'question'}
            start = await a.post(f'{base}/sessions/{sid}/runs', json=body, headers={'X-Request-ID': body['request_id']})
            await asyncio.wait_for(entered.wait(), 5)
            await a.aclose()
            assert service_running(config)
            run_id = start.json()['run_id']
            overlap = {'request_id': str(uuid4()), 'text': 'overlap'}
            assert (await b.post(f'{base}/sessions/{sid}/runs', json=overlap,
                headers={'X-Request-ID': overlap['request_id']})).json()['error']['code'] == 'session_busy'
            release.set()
            result = await b.get(f'{base}/runs/{run_id}/events')
            assert 'run_finished' in result.text
            assert (await b.get(f'{base}/runs/{run_id}')).json()['status'] == 'completed'
    asyncio.run(scenario())
