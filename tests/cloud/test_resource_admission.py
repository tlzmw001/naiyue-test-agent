"""Low disk after admission cannot reuse an old transfer to allocate more bytes."""
import asyncio
from uuid import uuid4

import httpx
import pytest

from cloud_helpers import owner_headers
from lifecycle_helpers import Backend, command, config, register
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_resources import ResourceLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from test_artifact_api import accept, metadata, prepare, task_headers, upload
from test_input_formats import package
from test_input_import import transfer_request
from test_runner_control import client, connect, prepared


def low_disk(runtime):
    runtime.resources.limits = ResourceLimits(min_free_bytes=2 ** 60)


def test_transfer_created_before_low_watermark_cannot_upload_but_can_cancel(tmp_path):
    captured, _, _ = package(tmp_path)
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            owner, _ = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),
                    base_url=runtime.settings.origin, headers=owner_headers(runtime)) as c:
                original = transfer_request(captured)
                created = await c.post('/api/cloud/v1/transfers', json=original)
                assert created.status_code == 201
                transfer = created.json()['result']['transfer']; ident = transfer['transfer_id']
                low_disk(runtime)
                assert (await c.post('/api/cloud/v1/transfers', json=original)).json() == created.json()
                rejected = await c.post('/api/cloud/v1/transfers', json=transfer_request(captured))
                assert rejected.status_code == 507 and rejected.json()['error']['code'] == 'resource_disk_capacity'
                consumed = []
                async def body():
                    consumed.append(True)
                    yield (captured.directory / 'archive').read_bytes()
                response = await c.put(f'/api/cloud/v1/transfers/{ident}/bytes', content=body(), headers={
                    'Content-Length': str(captured.size), 'Content-Type': 'application/octet-stream',
                    'Upload-Offset': '0', 'X-Content-SHA256': captured.archive_sha256})
                assert response.status_code == 507 and consumed == []
                assert runtime.inputs.transfer(ident).offset == 0
                assert not (runtime.settings.data_dir / 'transfers' / ident / 'archive').exists()
                assert (await c.delete('/api/cloud/v1/transfers/' + ident)).status_code == 200
        asyncio.run(run())


def test_confirmed_input_segment_replay_works_at_low_watermark(tmp_path):
    captured, _, _ = package(tmp_path)
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            owner, _ = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),
                    base_url=runtime.settings.origin, headers=owner_headers(runtime)) as c:
                transfer = (await c.post('/api/cloud/v1/transfers', json=transfer_request(captured))).json()['result']['transfer']
                route = '/api/cloud/v1/transfers/' + transfer['transfer_id'] + '/bytes'
                body = (captured.directory / 'archive').read_bytes()
                headers = {'Content-Type': 'application/octet-stream', 'Upload-Offset': '0',
                    'X-Content-SHA256': captured.archive_sha256}
                first = await c.put(route, content=body, headers=headers)
                assert first.status_code == 200
                low_disk(runtime)
                replay = await c.put(route, content=body, headers=headers)
                assert replay.status_code == 200 and replay.json() == first.json()
        asyncio.run(run())


def test_task_create_and_start_refuse_disk_but_stop_and_original_receipt_remain_available(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                record = register(runtime)
                request = command(record)
                low_disk(runtime)
                with pytest.raises(CloudError, match='resource_disk_capacity'):
                    runtime.inputs.create_task(dict(command_id=str(uuid4()), project_id=record.project_id,
                        input_id=record.input_id, template_id=record.template_id))
                with pytest.raises(CloudError, match='resource_disk_capacity'):
                    await runtime.lifecycle.command(record.task_id, request)
                assert not backend.calls
                assert runtime.registry.get_task(record.task_id) == record
                runtime.resources.limits = ResourceLimits()
                first = await runtime.lifecycle.command(record.task_id, request)
                low_disk(runtime)
                assert await runtime.lifecycle.command(record.task_id, request) == first
                current = runtime.registry.get_task(record.task_id)
                stopped = await runtime.lifecycle.command(record.task_id, command(current, 'stop_environment'))
                assert stopped.status == 'succeeded' and runtime.registry.get_task(record.task_id).sandbox_id is None
        asyncio.run(run())


def test_old_unknown_start_reconcile_does_not_allocate_after_disk_becomes_low(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend(); backend.lose_create_ack = True
            async with open_cloud_backend(runtime, backend=backend):
                record = register(runtime); request = command(record)
                original = await runtime.lifecycle.command(record.task_id, request)
                assert original.status == 'unknown'
                before = list(backend.calls)
                low_disk(runtime)
                result = await runtime.lifecycle.reconcile(request['command_id'])
                assert result.status == 'unknown' and result.error_code == 'resource_disk_capacity'
                assert runtime.resources.capacity()['counts']['active'] == 1
                assert backend.calls == before
        asyncio.run(run())


def test_refresh_cannot_bypass_disk_gate_for_input_install(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                record = register(runtime)
                await runtime.lifecycle.command(record.task_id, command(record))
                current = runtime.registry.get_task(record.task_id)
                before = list(backend.calls)
                low_disk(runtime)
                receipt = await runtime.lifecycle.command(record.task_id, command(current, 'refresh'))
                assert receipt.error_code == 'resource_disk_capacity'
                assert backend.calls == before
        asyncio.run(run())


@pytest.mark.parametrize('stage', ['capture', 'prepare', 'upload'])
def test_artifact_space_gate_on_all_allocation_stages(tmp_path, stage):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        binding = prepared(runtime)
        with client(runtime) as c, connect(c, runtime, binding) as ws:
            if stage == 'capture':
                from test_runner_control import hello
                greeting = hello(binding); greeting['capabilities'] = ['artifacts:capture']
                ws.send_json(greeting); ws.receive_json()
                task = runtime.registry.get_task(binding.task_id)
                low_disk(runtime)
                response = c.post('/api/cloud/v1/tasks/' + binding.task_id + '/artifacts', headers=owner_headers(runtime),
                    json=dict(command_id=str(uuid4()), expected_revision=task.revision,
                        expected_generation=task.generation, relative_name='result.txt'))
                assert response.status_code == 507
                assert runtime.registry.get_task(binding.task_id).artifact_operation_id is None
            else:
                capture, _ = accept(runtime, c, ws, binding)
                data = b'report'; record = metadata(binding, capture, data)
                path = '/internal/cloud/v1/tasks/' + binding.task_id + '/artifacts/' + record['artifact_id']
                if stage == 'prepare':
                    low_disk(runtime)
                    response = c.post(path + '/prepare', headers=task_headers(runtime, binding), json=record)
                else:
                    prepare(c, runtime, binding, record)
                    low_disk(runtime)
                    response = upload(c, runtime, binding, path, record, data)
                    assert not (runtime.settings.data_dir / 'artifact-transfers' / binding.task_id /
                        record['artifact_id'] / 'bytes').exists()
                assert response.status_code == 507


def test_low_disk_does_not_block_necessary_history_ingress(tmp_path):
    from test_history_ingress import setup_task, batch
    with open_cloud_runtime(config(tmp_path)) as runtime:
        task, raw = setup_task(runtime)
        low_disk(runtime)
        acknowledgement = runtime.append_events(raw, batch(task))
        assert acknowledgement['last_seq'] == 1
        assert len(runtime.history_page(task.task_id)['events']) == 1


@pytest.mark.parametrize('published', [False, True])
def test_uploaded_artifact_necessary_publish_and_index_recovery_survive_low_disk(tmp_path, monkeypatch, published):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        binding = prepared(runtime)
        with client(runtime) as c, connect(c, runtime, binding) as ws:
            capture, _ = accept(runtime, c, ws, binding)
            data = b'confirmed'; record = metadata(binding, capture, data)
            path = prepare(c, runtime, binding, record)
            assert upload(c, runtime, binding, path, record, data).status_code == 200
            if published:
                transfer = runtime.artifacts.repository.get(binding.task_id, record['artifact_id'])
                runtime.artifacts.store.publish(transfer.artifact)
            low_disk(runtime)
            assert c.post(path + '/commit', headers=task_headers(runtime, binding), json={}).status_code == 200
            result = c.get('/api/cloud/v1/tasks/' + binding.task_id + '/artifacts/' + record['artifact_id'] + '/bytes',
                headers=owner_headers(runtime))
            assert result.content == data


def test_retained_workspace_restart_does_not_count_input_bytes_twice(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = register(runtime)
                await runtime.lifecycle.command(task.task_id, command(task))
                current = runtime.registry.get_task(task.task_id)
                await runtime.lifecycle.command(task.task_id, command(current, 'stop_environment'))
                task = runtime.registry.get_task(task.task_id)
                usage = runtime.resources.disk(refresh=True)
                runtime.resources.limits = ResourceLimits(max_logical_bytes=usage.logical_bytes + 1)
                assert (await runtime.lifecycle.command(task.task_id, command(task))).status == 'succeeded'
        asyncio.run(run())


@pytest.mark.parametrize('published', [False, True])
def test_uploaded_input_publish_and_missing_index_recover_below_watermark(tmp_path, monkeypatch, published):
    captured, _, _ = package(tmp_path)
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            owner, _ = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),
                    base_url=runtime.settings.origin, headers=owner_headers(runtime)) as c:
                transfer = (await c.post('/api/cloud/v1/transfers', json=transfer_request(captured))).json()['result']['transfer']
                route = '/api/cloud/v1/transfers/' + transfer['transfer_id']
                body = (captured.directory / 'archive').read_bytes()
                response = await c.put(route + '/bytes', content=body, headers={'Content-Type': 'application/octet-stream',
                    'Upload-Offset': '0', 'X-Content-SHA256': captured.archive_sha256})
                assert response.status_code == 200
                if published:
                    original = runtime.inputs.repository.commit
                    def unavailable(*args):raise OSError('controlled metadata failure after publication')
                    monkeypatch.setattr(runtime.inputs.repository, 'commit', unavailable)
                    assert (await c.post(route + '/commit', json={})).status_code == 503
                    monkeypatch.setattr(runtime.inputs.repository, 'commit', original)
                    assert (runtime.settings.data_dir / 'inputs' / captured.input_id).is_dir()
                low_disk(runtime)
                result = await c.post(route + '/commit', json={})
                assert result.status_code == 200 and result.json()['input_id'] == captured.input_id
                assert runtime.inputs.transfer(transfer['transfer_id']).state == 'committed'
        asyncio.run(run())
