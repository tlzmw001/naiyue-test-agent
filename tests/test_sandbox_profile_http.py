"""Profile refresh and choice retain the shared service authentication boundary."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.ownership import read_discovery
from naiyue_test_agent.web.schemas import NewSession
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog
from test_service_ownership import options
from git_input_helpers import repository


@pytest.mark.parametrize('change', [dict(execution='host', profile_id='python-tests', expected_profile_revision='0'*64),
    dict(profile_id='python-tests'), dict(expected_profile_revision='0'*64),
    dict(profile_id='../escape', expected_profile_revision='0'*64)])
def test_profile_request_contract_rejects_incomplete_or_host_choice(change):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        NewSession.model_validate({'model_id': 'fake', 'execution': 'sandbox', **change})


def test_refresh_is_fixed_path_authenticated_atomic_and_does_not_probe_docker(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            config = options(tmp_path)
            server = create_service_app(config, application_factory=lambda: application(tmp_path, catalog()))
            async with server.router.lifespan_context(server):
                record = read_discovery(config); app = server.state.application
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                config.settings_path.write_text('[sandbox]\nimage="fixture:reloaded"\n')
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url=record.origin,
                    headers={'Authorization': 'Bearer '+record.access_token, 'X-Service-Instance': record.instance_id,
                        'Origin': record.origin}) as client:
                    def command(**headers): return {'X-Request-ID': str(uuid4()), **headers}
                    path = '/api/v1/environments/refresh'
                    denied = await client.post(path, json={}, headers=command(Authorization=''))
                    assert denied.status_code == 401
                    denied = await client.post(path, json={}, headers=command(**{'X-Service-Instance': str(uuid4())}))
                    assert denied.status_code == 409
                    denied = await client.post(path, json={'settings_path': '/arbitrary'}, headers=command())
                    assert denied.status_code == 422
                    assert app.environments._service.configuration.default_profile == 'python-tests'
                    # An unrelated invalid setting must not be hot-applied or reject sandbox refresh.
                    config.settings_path.write_text('[storage]\nunknown=true\n[sandbox]\nimage="fixture:reloaded"\n')
                    key = command()
                    refreshed = await client.post(path, json={}, headers=key)
                    assert refreshed.status_code == 200, refreshed.text
                    assert refreshed.json()['default_profile'] == 'default'
                    assert set(refreshed.json()['profiles'][0]) == {'profile_id', 'name', 'revision'}
                    config.settings_path.write_text('malformed TOML')
                    assert (await client.post(path, json={}, headers=key)).json() == refreshed.json()
                    failed = await client.post(path, json={}, headers=command())
                    assert failed.status_code == 409 and failed.json()['error']['code'] == 'env_configuration_invalid'
                    assert app.environments._service.configuration.select().settings.image == 'fixture:reloaded'
                    assert transport.created == []
                    choice = (await client.get(f'/api/v1/projects/{project.project_id}/execution-options')).json()
                    assert choice['profiles'] == refreshed.json()['profiles']
                    config.settings_path.write_text('')
                    assert (await client.post(path, headers=command())).json()['profiles'] == []
                    assert (await client.get(f'/api/v1/projects/{project.project_id}/execution-options')).json()['sandbox_available'] is False
    asyncio.run(scenario())


def test_http_explicit_choice_checks_fingerprint_and_replays_original_request(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            config = options(tmp_path)
            server = create_service_app(config, application_factory=lambda: application(tmp_path, catalog()))
            async with server.router.lifespan_context(server):
                record = read_discovery(config); app = server.state.application
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url=record.origin,
                    headers={'Authorization': 'Bearer '+record.access_token, 'X-Service-Instance': record.instance_id,
                        'Origin': record.origin}) as client:
                    prefix = f'/api/v1/scopes/{project.project_id}'
                    choice = (await client.get(f'/api/v1/projects/{project.project_id}/execution-options')).json()
                    selected = choice['profiles'][1]
                    body = dict(model_id='fake', execution='sandbox', profile_id=selected['profile_id'],
                        expected_profile_revision=selected['revision'])
                    stale = await client.post(prefix+'/sessions', json={**body, 'expected_profile_revision': '0'*64},
                        headers={'X-Request-ID': str(uuid4())})
                    assert stale.status_code == 409 and stale.json()['error']['code'] == 'env_profile_changed'
                    assert transport.created == [] and (await client.get(prefix+'/sessions')).json() == []
                    key = {'X-Request-ID': str(uuid4())}
                    first = await client.post(prefix+'/sessions', json=body, headers=key)
                    assert first.status_code == 201, first.text
                    config.settings_path.write_text('')
                    await client.post('/api/v1/environments/refresh', headers={'X-Request-ID': str(uuid4())})
                    assert (await client.post(prefix+'/sessions', json=body, headers=key)).json() == first.json()
                    conflict = await client.post(prefix+'/sessions', json={**body, 'profile_id': 'python-tests'}, headers=key)
                    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'request_id_conflict'
                    assert len(transport.created) == 1
                    saved = (await app.environments.list(project.project_id))[0]
                    assert saved.profile_id == 'frontend-tests' and saved.settings['image'] == 'fixture:node'
    asyncio.run(scenario())
