import asyncio
from uuid import uuid4

import httpx
import pytest

from cloud_helpers import owner_headers
from lifecycle_helpers import Backend, command, config, register
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.service.cloud.app import create_cloud_apps


def request(runtime, task_id, **changes):
    preview = runtime.deletions.preview(task_id)
    return dict(command_id=str(uuid4()), confirm_task_id=task_id,
        expected_revision=preview['revision'], expected_generation=preview['generation'],
        preview_digest=preview['preview_digest'], acknowledge_unknown=False) | changes


def test_delete_cleans_only_owned_task_and_preserves_shared_input_and_original_id(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = register(runtime); other = register(runtime)
                runtime.history_page(task.task_id)
                shared = runtime.settings.data_dir / 'inputs' / task.input_id
                parent = runtime.settings.data_dir / 'tasks' / task.task_id
                parent.mkdir(parents=True); (parent / 'report').write_text('remove')
                original = request(runtime, task.task_id)
                receipt = await runtime.deletions.command(task.task_id, original)
                assert receipt.status == 'succeeded' and receipt.result['phase'] == 'finished'
                tombstone = receipt.result['tombstone']
                assert tombstone['instance_id'] == runtime.instance_id
                assert tombstone['task_id'] == task.task_id
                assert tombstone['delete_command_id'] == original['command_id']
                assert not parent.exists() and shared.exists()
                assert runtime.registry.get_task(other.task_id).lifecycle != 'deleted'
                assert runtime.registry.get_task(task.task_id).lifecycle == 'deleted'
                assert runtime.resources.capacity()['counts']['retained'] == 1
                assert await runtime.deletions.command(task.task_id, original) == receipt
                with pytest.raises(CloudError, match='task_not_available'):
                    runtime.history_page(task.task_id)
                assert not (runtime.settings.data_dir / 'history' / (task.task_id + '.sqlite')).exists()
        asyncio.run(run())


@pytest.mark.parametrize('field,value', [('active_run_id', 'a' * 32), ('reclaim_blockers', ['required_save']),
    ('artifact_operation_id', str(uuid4())), ('control_operation_id', str(uuid4()))])
def test_normal_delete_requires_stop_and_necessary_save(tmp_path, field, value):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = register(runtime)
                runtime.replace_task(task.model_copy(update={field: value, 'revision': 2}),
                    expected_revision=1, expected_generation=1)
                with pytest.raises(CloudError, match='task_delete_requires_stop'):
                    await runtime.deletions.command(task.task_id, request(runtime, task.task_id))
                assert runtime.registry.get_task(task.task_id).lifecycle != 'deleting'
        asyncio.run(run())


def test_stale_confirmation_and_wrong_task_fail_before_delete(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = register(runtime); original = request(runtime, task.task_id)
                with pytest.raises(CloudError, match='task_delete_confirmation_changed'):
                    await runtime.deletions.command(task.task_id, original | {'confirm_task_id': 'f' * 32})
                runtime.replace_task(task.model_copy(update={'revision': 2}), expected_revision=1, expected_generation=1)
                with pytest.raises(CloudError, match='task_revision_conflict'):
                    await runtime.deletions.command(task.task_id, original)
        asyncio.run(run())


def test_owner_routes_authenticate_and_task_listener_has_no_delete(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = register(runtime); owner, internal = create_cloud_apps(runtime)
                route = '/api/cloud/v1/tasks/' + task.task_id
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner), base_url=runtime.settings.origin) as c:
                    assert (await c.get(route + '/delete-preview')).status_code == 401
                    preview = await c.get(route + '/delete-preview', headers=owner_headers(runtime))
                    assert preview.status_code == 200
                    response = await c.post(route + '/delete', headers=owner_headers(runtime), json=request(runtime, task.task_id))
                    assert response.status_code == 200 and response.json()['status'] == 'succeeded'
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=internal), base_url='http://' + runtime.settings.task_listen) as c:
                    assert (await c.post(route + '/delete', json={})).status_code == 404
        asyncio.run(run())
