import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CreateProject
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import settings, owner_headers, task_record


def test_project_ack_loss_reopen_same_receipt_and_capacity(tmp_path):
    config = settings(tmp_path, max_receipts=1)
    command = CreateProject(command_id=str(uuid4()), name='接口回归')
    with open_cloud_runtime(config) as runtime:
        first = runtime.commands.create_project(command)
        assert runtime.commands.create_project(command) == first
        with pytest.raises(CloudError, match='command_id_conflict'):
            runtime.commands.create_project(CreateProject(command_id=command.command_id, name='changed'))
        with pytest.raises(CloudError, match='command_capacity'):
            runtime.commands.create_project(CreateProject(command_id=str(uuid4()), name='second'))
    with open_cloud_runtime(config) as runtime:
        assert runtime.registry.get_command(command.command_id) == first
        assert runtime.commands.create_project(command) == first
        assert [p.project_id for p in runtime.registry.list_projects()] == [first.resource_id]


def test_concurrent_same_command_has_one_effect(tmp_path):
    command = CreateProject(command_id=str(uuid4()), name='parallel')
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(runtime.commands.create_project, [command] * 16))
        assert all(r == results[0] for r in results)
        assert len(runtime.registry.list_projects()) == 1


def test_receipt_insert_failure_rolls_back_project(tmp_path, caplog):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        runtime.database.connection.execute("""CREATE TRIGGER test_failure BEFORE INSERT ON commands
            BEGIN SELECT RAISE(ABORT,'PRIVATE_SQL_DETAIL'); END""")
        command = CreateProject(command_id=str(uuid4()), name='rollback')
        with pytest.raises(sqlite3.Error):
            runtime.commands.create_project(command)
        assert runtime.registry.list_projects() == []
        with pytest.raises(CloudError, match='command_not_found'):
            runtime.registry.get_command(command.command_id)
        runtime.database.connection.execute('DROP TRIGGER test_failure')
        assert runtime.commands.create_project(command).status == 'succeeded'


def test_http_routes_survive_reopen_and_do_not_advertise_future_operations(tmp_path):
    config = settings(tmp_path)
    command = {'command_id': str(uuid4()), 'name': 'HTTP'}
    async def run(runtime, expected=None):
        owner, task = create_cloud_apps(runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),
                base_url=runtime.settings.origin, headers=owner_headers(runtime)) as client:
            response = await client.post('/api/cloud/v1/projects', json=command)
            assert response.status_code == 201
            if expected is not None: assert response.json() == expected
            value = response.json()
            receipt = await client.get('/api/cloud/v1/commands/' + command['command_id'])
            assert receipt.json() == value['receipt']
            listed = (await client.get('/api/cloud/v1/projects')).json()
            assert listed == [value['project']]
            instance = (await client.get('/api/cloud/v1/instance')).json()
            assert instance['instance_id'] == runtime.instance_id
            assert 'tasks:run' not in instance['capabilities']
            assert 'tasks:create' in instance['capabilities']
            assert (await client.post('/api/cloud/v1/tasks', json={})).status_code == 422
            assert 'owner-test-only-secret' not in str(value) + str(instance)
            assert str(config.data_dir) not in str(value) + str(instance)
            return value
    with open_cloud_runtime(config) as runtime:
        first = asyncio.run(run(runtime))
    with open_cloud_runtime(config) as runtime:
        asyncio.run(run(runtime, first))


def test_task_cas_rejects_stale_and_keeps_immutable_baseline(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        project = runtime.commands.create_project(CreateProject(command_id=str(uuid4()), name='p'))
        task = task_record(project.resource_id)
        runtime.registry.add_task(task)
        replacement = task.model_copy(update={'revision': 2, 'generation': 2})
        runtime.replace_task(replacement, expected_revision=1, expected_generation=1)
        with pytest.raises(CloudError, match='task_revision_conflict'):
            runtime.replace_task(replacement, expected_revision=1, expected_generation=1)
        with pytest.raises(CloudError, match='task_immutable_identity'):
            runtime.replace_task(replacement.model_copy(update={'revision': 3, 'input_id': 'b' * 64}),
                expected_revision=2, expected_generation=2)
        assert runtime.registry.get_task(task.task_id) == replacement
