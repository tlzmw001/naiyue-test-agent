import asyncio
import time
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CreateProject
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import settings, owner_headers, task_record


def registered(runtime):
    project = runtime.commands.create_project(CreateProject(command_id=str(uuid4()), name='auth'))
    task = task_record(project.resource_id)
    runtime.registry.add_task(task)
    return task


@pytest.mark.parametrize('change,status,code', [({'Authorization': 'Bearer wrong'}, 401, 'unauthorized'),
    ({'Host': 'evil'}, 403, 'invalid_host'), ({'Origin': 'http://evil'}, 403, 'invalid_origin'),
    ({'X-Cloud-Protocol': '2'}, 409, 'cloud_protocol_mismatch'),
    ({'X-Service-Instance': str(uuid4())}, 409, 'cloud_instance_mismatch')])
def test_owner_rejections_have_no_command_side_effect(tmp_path, change, status, code):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        async def run():
            owner, task = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),
                    base_url=runtime.settings.origin) as client:
                response = await client.post('/api/cloud/v1/projects',
                    headers=owner_headers(runtime) | change,
                    json={'command_id': str(uuid4()), 'name': 'forbidden'})
                assert response.status_code == status
                assert response.json()['error']['code'] == code
        asyncio.run(run())
        assert runtime.registry.list_projects() == []


def test_task_secret_is_hashed_and_bound_to_task_purpose_generation_and_expiry(tmp_path, monkeypatch):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        first, second = registered(runtime), registered(runtime)
        issued = runtime.credentials.issue(first.task_id, scopes=('identity:read',))
        raw = issued.token.get_secret_value()
        assert raw not in repr(issued)
        with runtime.database.transaction() as connection:
            row = dict(connection.execute('SELECT * FROM task_credentials').fetchone())
        assert raw not in str(row)
        assert runtime.credentials.authorize(raw, first.task_id, 'identity:read').generation == 1
        for task_id, purpose in ((second.task_id, 'identity:read'), (first.task_id, 'events:append')):
            with pytest.raises(CloudError): runtime.credentials.authorize(raw, task_id, purpose)
        with pytest.raises(CloudError): runtime.credentials.issue(first.task_id, scopes=('owner:write',))
        runtime.credentials.revoke(first.task_id, issued.credential_id)
        with pytest.raises(CloudError): runtime.credentials.authorize(raw, first.task_id, 'identity:read')
        issued = runtime.credentials.issue(first.task_id, scopes=('identity:read',))
        with runtime.database.transaction(write=True) as connection:
            connection.execute('UPDATE task_credentials SET expires_at=?', (time.time() - 1,))
        with pytest.raises(CloudError):
            runtime.credentials.authorize(issued.token.get_secret_value(), first.task_id, 'identity:read')


def test_two_apps_have_disjoint_routes_and_credentials(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        record = registered(runtime)
        issued = runtime.credentials.issue(record.task_id, scopes=('identity:read',))
        raw = issued.token.get_secret_value()
        identity = '/internal/cloud/v1/tasks/' + record.task_id + '/identity'
        async def run():
            owner, task = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner), base_url=runtime.settings.origin) as o:
                assert (await o.get(identity, headers=owner_headers(runtime))).status_code == 404
                assert (await o.get('/api/cloud/v1/instance', headers={'Authorization': 'Bearer ' + raw})).status_code == 401
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task), base_url='http://' + runtime.settings.task_listen) as t:
                assert (await t.get('/api/cloud/v1/projects', headers=owner_headers(runtime))).status_code == 404
                assert (await t.get(identity, headers=owner_headers(runtime))).status_code == 401
                response = await t.get(identity, headers={'Authorization': 'Bearer ' + raw})
                assert response.status_code == 200
                assert response.json()['task_id'] == record.task_id
                assert raw not in response.text and str(runtime.settings.data_dir) not in response.text
        asyncio.run(run())


def test_authenticated_initial_discovery_does_not_require_instance_header(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        async def run():
            owner, _ = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner), base_url=runtime.settings.origin) as client:
                response = await client.get('/api/cloud/v1/instance', headers={
                    'Authorization': 'Bearer ' + runtime.settings.access_token})
                assert response.status_code == 200
        asyncio.run(run())
