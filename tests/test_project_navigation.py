"""Sidebar metadata is isolated from execution-host creation and model loading."""
import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent import application
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.config_paths import StorageSettings
from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.ownership import read_discovery
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.storage.project_store import ProjectStore
from test_service_ownership import options
from test_service_protocol import command, service_clients
from test_async_application import StreamingToolModel


@asynccontextmanager
async def directory_client(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: calls.append('model'))
    config = options(tmp_path)
    config.frontend_dist.mkdir()
    (config.frontend_dist / 'index.html').write_text('<html></html>')
    @asynccontextmanager
    async def factory():
        settings = AppSettings(storage=StorageSettings(data_dir=config.data_dir))
        async with open_service_application('unused', tools=(), system_prompt='',
                response_format=None, settings=settings) as app:
            yield app
    app = create_service_app(config, application_factory=factory)
    async with app.router.lifespan_context(app):
        record = read_discovery(config)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=record.origin,
                headers={'Authorization': f'Bearer {record.access_token}',
                         'X-Service-Instance': record.instance_id}) as client:
            yield client, app, calls


def test_cold_navigation_loads_no_models_hosts_or_chats_database(monkeypatch, tmp_path):
    async def scenario():
        async with directory_client(monkeypatch, tmp_path) as (client, app, calls):
            root = tmp_path / 'project'; root.mkdir()
            p = await app.state.application.projects.add_local(root, trusted=True, operation_id='add')
            assert (await client.get('/api/v1/navigation/chats/sessions')).json() == []
            response = await client.get(f'/api/v1/navigation/{p.project_id}/sessions')
            assert response.status_code == 200 and response.json() == []
            assert calls == [] and app.state.scopes.services == {}
            assert app.state.application._lifetime._children == []
            assert not (app.state.application.projects._service.data_dir / 'chats/sessions.sqlite').exists()
            assert (await client.get('/api/v1/navigation/chats/sessions', headers={'Authorization': ''})).status_code == 401
            assert (await client.get('/api/v1/navigation/chats/sessions', headers={'X-Service-Instance': '0' * 32})).status_code == 409
            assert (await client.get('/api/v1/navigation/unknown/sessions')).status_code == 404
            assert (await client.get(f'/api/v1/navigation/{uuid4()}/sessions')).status_code == 404
    asyncio.run(scenario())


def test_navigation_reuses_hot_owner_and_keeps_project_sessions_separate(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, _, app, _):
            registered = []
            for name in ('a', 'b'):
                root = tmp_path / name; root.mkdir()
                p = await app.state.application.projects.add_local(root, trusted=True, operation_id=name)
                response = await client.post(f'/api/v1/scopes/{p.project_id}/sessions', json={'model_id': 'A'}, headers=command())
                assert response.status_code == 201
                registered.append((p, response.json()['session_id']))
            for p, sid in registered:
                result = await client.get(f'/api/v1/navigation/{p.project_id}/sessions')
                assert result.status_code == 200
                assert [r['session_id'] for r in result.json()] == [sid]
                assert 'thread_id' not in result.json()[0]
                assert 'active_tool_names_json' not in result.json()[0]
                assert result.json()[0]['pinned'] is False
                assert result.json()[0]['execution_binding']=='project_local'
                active=(await client.get(f'/api/v1/scopes/{p.project_id}/sessions')).json()
                assert active[0]['execution_binding']=='project_local'
            assert (await client.get('/api/v1/navigation/chats/sessions')).json() == []
            assert len(app.state.scopes.services) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['missing', 'wrong_project', 'untrusted_missing_root'])
def test_cold_directory_errors_do_not_fall_back_or_require_execution(monkeypatch, tmp_path, failure):
    async def scenario():
        async with directory_client(monkeypatch, tmp_path) as (client, app, calls):
            root = tmp_path / 'project'; root.mkdir()
            p = await app.state.application.projects.add_local(root, trusted=True, operation_id='add')
            if failure == 'missing':
                p.database_path.unlink()
            elif failure == 'wrong_project':
                with ProjectStore(p.database_path) as store:
                    store.database.connection.execute('UPDATE naiyue_workspace SET workspace_id=?', (str(uuid4()),))
                    store.database.connection.commit()
            else:
                with app.state.application.projects._service.registry.database.transaction(write=True) as connection:
                    connection.execute('UPDATE projects SET trusted=0 WHERE project_id=?', (p.project_id,))
                root.rmdir()
            response = await client.get(f'/api/v1/navigation/{p.project_id}/sessions')
            assert response.status_code == (200 if failure == 'untrusted_missing_root' else 409)
            if failure == 'untrusted_missing_root': assert response.json() == []
            else: assert 'error' in response.json()
            assert calls == [] and app.state.scopes.services == {}
            assert not (app.state.application.projects._service.data_dir / 'chats/sessions.sqlite').exists()
    asyncio.run(scenario())


def test_untrusted_project_history_remains_readable_without_execution_or_project_instructions(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[])
        async with service_clients(monkeypatch, tmp_path, model) as (client, _, app, _):
            root = tmp_path / 'untrusted'; root.mkdir()
            p = await app.state.application.projects.add_local(root, trusted=True, operation_id='add')
            base = f'/api/v1/scopes/{p.project_id}'
            sid = (await client.post(base + '/sessions', json={'model_id': 'A'}, headers=command())).json()['session_id']
            await app.state.scopes.close_project(p.project_id)
            service = app.state.application.projects._service
            with service.registry.database.transaction(write=True) as connection:
                connection.execute('UPDATE projects SET trusted=0 WHERE project_id=?', (p.project_id,))
            from naiyue_test_agent.application.sessions import project_instructions
            def unexpected(*args): raise AssertionError('history must not read project instructions')
            monkeypatch.setattr(project_instructions, 'load_project_instruction_part', unexpected)
            directory = await client.get(f'/api/v1/navigation/{p.project_id}/sessions')
            assert directory.status_code == 200 and directory.json()[0]['session_id'] == sid
            history = await client.post(f'{base}/sessions/{sid}/prepare', headers=command())
            assert history.status_code == 200
            assert history.json()['can_run'] is False
            assert history.json()['execution_root_error']['code'] == 'execution_root_unavailable'
            request_id = str(uuid4())
            run = await client.post(f'{base}/sessions/{sid}/runs', json={'request_id': request_id, 'text': 'must refuse'}, headers={'X-Request-ID': request_id})
            assert run.status_code == 409 and run.json()['error']['code'] == 'execution_root_unavailable'
            assert model.seen == []
    asyncio.run(scenario())
