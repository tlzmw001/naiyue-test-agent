"""Two authenticated clients share one owner and explicitly bound host APIs."""
import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage

from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.ownership import read_discovery, service_running
from test_async_application import StreamingToolModel
from test_multi_project_runtime import multi_app, projects
from test_service_ownership import options


@asynccontextmanager
async def service_clients(monkeypatch, tmp_path, model, **changes):
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'index.html').write_text('<html>fixture</html>')
    config = options(tmp_path, **changes)
    @asynccontextmanager
    async def factory():
        async with multi_app(monkeypatch, tmp_path, model) as (application, calls):
            yield application
    app = create_service_app(config, application_factory=factory)
    async with app.router.lifespan_context(app):
        record = read_discovery(config)
        headers = {'Authorization': f'Bearer {record.access_token}', 'Origin': record.origin}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url=record.origin, headers=headers) as a:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url=record.origin, headers=headers) as b:
                yield a, b, app, config
    assert not service_running(config)


def command():
    return {'X-Request-ID': str(uuid4())}


def test_handshake_and_existing_auth_boundaries(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, b, app, config):
            body = config.identity.model_dump(mode='json')
            x = await a.post('/api/v1/handshake', json=body)
            y = await b.post('/api/v1/handshake', json=body)
            assert x.status_code == 200 and x.json() == y.json()
            assert x.json()['instance_id'] == read_discovery(config).instance_id
            assert 'access_token' not in x.text
            for patch, code in [({'product_version': 'different'}, 'service_version_mismatch'),
                    ({'protocol_version': 99}, 'service_protocol_mismatch'),
                    ({'data_dir': '/another'}, 'service_data_dir_mismatch'),
                    ({'config_paths': {'models': '/other', 'settings': str(config.settings_path)}}, 'service_config_mismatch')]:
                response = await a.post('/api/v1/handshake', json={**body, **patch})
                assert response.status_code == 409 and response.json()['error']['code'] == code
            url = '/api/v1/scopes/chats/sessions'
            assert (await a.get(url, headers={'Authorization': ''})).status_code == 401
            assert (await a.get(url, headers={'Host': 'evil.example'})).status_code == 403
            assert (await a.post(url, json={'model_id': 'A'}, headers={**command(), 'Origin': 'https://evil.example'})).status_code == 403
            assert app.state.scopes.services == {}
    asyncio.run(scenario())


def test_two_clients_share_chats_run_receipt_history_and_events(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='answer')])
        async with service_clients(monkeypatch, tmp_path, model) as (a, b, app, _):
            base = '/api/v1/scopes/chats'
            headers = command()
            first, duplicate = await asyncio.gather(
                a.post(base + '/sessions', json={'model_id': 'A'}, headers=headers),
                b.post(base + '/sessions', json={'model_id': 'A'}, headers=headers))
            assert first.status_code == duplicate.status_code == 201
            assert first.json() == duplicate.json()
            sid = first.json()['session_id']
            assert len((await b.get(base + '/sessions')).json()) == 1
            body = {'request_id': str(uuid4()), 'text': 'question'}
            headers = {'X-Request-ID': body['request_id']}
            start, repeat = await asyncio.gather(
                a.post(f'{base}/sessions/{sid}/runs', json=body, headers=headers),
                b.post(f'{base}/sessions/{sid}/runs', json=body, headers=headers))
            assert start.status_code == repeat.status_code == 202
            assert start.json() == repeat.json()
            rid = start.json()['run_id']
            x, y = await asyncio.gather(a.get(f'{base}/runs/{rid}/events'), b.get(f'{base}/runs/{rid}/events'))
            assert x.text == y.text and 'run_finished' in x.text
            assert len(model.seen) == 1
            view = (await b.get(f'{base}/sessions/{sid}')).json()
            assert [m['content'] for m in view['messages']] == ['question', 'answer']
            conflict = await b.post(f'{base}/sessions/{sid}/runs', json={**body, 'text': 'changed'}, headers=headers)
            assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'request_id_conflict'
            cursor = (await b.get(f'{base}/runs/{rid}')).json()['last_sequence']
            assert (await a.get(f'{base}/runs/{rid}/events?after={cursor}')).text == ''
            assert len(app.state.scopes.services) == 1
    asyncio.run(scenario())


def test_scoped_projects_do_not_leak_ids_and_chats_cannot_open_project_history(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, b, app, _):
            (pa, _), (pb, _) = await projects(app.state.application, tmp_path)
            left, right = f'/api/v1/scopes/{pa.project_id}', f'/api/v1/scopes/{pb.project_id}'
            sid = (await a.post(left + '/sessions', json={'model_id': 'A'}, headers=command())).json()['session_id']
            assert (await b.get(right + f'/sessions/{sid}')).status_code == 404
            assert (await b.get('/api/v1/scopes/chats' + f'/sessions/{sid}/messages')).status_code == 404
            assert (await b.get('/api/v1/scopes/unknown/sessions')).status_code == 404
            assert (await b.get(left + '/models')).json()['model_ids'] == ['A']
            assert len((await a.get('/api/v1/projects')).json()['items']) == 2
            assert (await b.get(right + '/sessions')).json() == []
    asyncio.run(scenario())


def test_mutations_require_consistent_ids_and_receipts_are_bounded(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[]), max_receipts=1) as (a, b, _, _):
            url = '/api/v1/scopes/chats/sessions'
            assert (await a.post(url, json={'model_id': 'A'})).status_code == 422
            assert (await a.post(url, json={'model_id': 'A'}, headers={'X-Request-ID': 'invalid'})).status_code == 422
            headers = command()
            first = await a.post(url, json={'model_id': 'A'}, headers=headers)
            assert first.status_code == 201
            assert (await b.post(url, json={'model_id': 'A'}, headers=headers)).json() == first.json()
            full = await a.post(url, json={'model_id': 'A'}, headers=command())
            assert full.status_code == 409 and full.json()['error']['code'] == 'request_window_full'
            assert len((await b.get(url)).json()) == 1
    asyncio.run(scenario())


def test_project_management_commands_reuse_operation_contract(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, b, _, _):
            root = tmp_path / 'local'
            root.mkdir()
            headers = command()
            body = {'root': str(root), 'trusted': True, 'name': 'Local'}
            first = await a.post('/api/v1/projects/local', json=body, headers=headers)
            repeat = await b.post('/api/v1/projects/local', json=body, headers=headers)
            assert first.status_code == 201 and first.json() == repeat.json()
            pid = first.json()['project_id']
            assert (await b.get(f'/api/v1/projects/{pid}')).json()['name'] == 'Local'
            assert not (root / '.naiyue/workspace.json').exists()
            assert (await a.patch(f'/api/v1/projects/{pid}', json={'name': 'Renamed'}, headers=command())).status_code == 200
            assert (await b.get(f'/api/v1/projects/{pid}')).json()['name'] == 'Renamed'
    asyncio.run(scenario())


def test_receipt_byte_budget_refuses_new_effects_before_execution(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[]),
                max_frame_bytes=1024, receipt_cache_bytes=1024) as (a, b, _, _):
            url = '/api/v1/scopes/chats/sessions'
            headers = command()
            first = await a.post(url, json={'model_id': 'A'}, headers=headers)
            assert first.status_code == 201
            assert (await b.post(url, json={'model_id': 'A'}, headers=headers)).json() == first.json()
            full = await a.post(url, json={'model_id': 'A'}, headers=command())
            assert full.status_code == 409 and full.json()['error']['code'] == 'request_window_full'
            assert len((await b.get(url)).json()) == 1
    asyncio.run(scenario())


def test_handshake_is_authenticated_and_bounded_before_body_parsing(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[]), max_frame_bytes=1024) as (a, _, app, _):
            huge = {'padding': 'x' * 4096}
            assert (await a.post('/api/v1/handshake', json=huge, headers={'Authorization': ''})).status_code == 401
            assert (await a.post('/api/v1/handshake', json=huge)).status_code == 413
            assert app.state.scopes.services == {}
            for invalid in (12, [], {}):
                response = await a.post('/api/v1/scopes/chats/sessions',
                    json={'model_id': 'A', 'request_id': invalid}, headers=command())
                assert response.status_code == 422
    asyncio.run(scenario())


def test_mcp_refresh_reads_the_service_settings_source(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, _, app, config):
            service = await app.state.scopes.get('chats')
            refresh = AsyncMock(return_value={'servers': []})
            monkeypatch.setattr(service.application.mcp, 'refresh', refresh)
            result = await a.post('/api/v1/scopes/chats/mcp/refresh', json={}, headers=command())
            assert result.status_code == 200
            refresh.assert_awaited_once_with(config.settings_path)
    asyncio.run(scenario())


def test_scoped_replay_expiry_requires_snapshot_without_stopping_run(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='answer')])) as (a, b, app, _):
            base = '/api/v1/scopes/chats'
            service = await app.state.scopes.get('chats')
            service.delivery.settings = service.settings.model_copy(update={'event_capacity': 2})
            sid = (await a.post(base + '/sessions', json={'model_id': 'A'}, headers=command())).json()['session_id']
            body = {'request_id': str(uuid4()), 'text': 'question'}
            run = (await a.post(f'{base}/sessions/{sid}/runs', json=body,
                headers={'X-Request-ID': body['request_id']})).json()['run_id']
            await service.delivery.wait(run)
            expired = await b.get(f'{base}/runs/{run}/events?after=0')
            assert expired.status_code == 409 and expired.json()['error']['code'] == 'resync_required'
            view = (await b.get(f'{base}/sessions/{sid}')).json()
            assert [m['content'] for m in view['messages']] == ['question', 'answer']
            assert view['live']['status'] == 'completed'
            assert (await a.get('/api/v1/scopes/00000000-0000-0000-0000-000000000000/runs/' + run)).status_code == 404
    asyncio.run(scenario())
