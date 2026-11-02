"""Resource policy uses temporary managed files and the controlled backend only."""
import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from cloud_helpers import owner_headers, settings, task_record
from lifecycle_helpers import Backend, command, config, register
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_resources import ResourceLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CreateProject
from naiyue_test_agent.service.cloud.app import create_cloud_apps


@pytest.mark.parametrize('field,value', [('maintenance_interval_seconds', 0), ('task_batch_size', 0),
    ('scan_max_entries', 0), ('scan_max_depth', 0), ('scan_timeout_seconds', 0),
    ('scan_cache_seconds', -1), ('min_free_bytes', -1), ('max_logical_bytes', 0)])
def test_resource_configuration_rejects_unbounded_or_invalid_values(field, value):
    with pytest.raises(ValidationError):
        ResourceLimits(**{field: value})


def test_capacity_owner_only_known_counts_and_unknown_keep_occupied(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        project = runtime.commands.create_project(CreateProject(command_id=str(uuid4()), name='capacity'))
        for fields in [dict(lifecycle='ready', sandbox_id='one', environment_phase='backend_ready'),
                dict(lifecycle='starting', environment_operation_id=str(uuid4()), environment_phase='preparing'),
                dict(lifecycle='reconciling', environment_phase='unknown'), dict(lifecycle='stopped'),
                dict(lifecycle='deleted')]:
            runtime.registry.add_task(task_record(project.resource_id, **fields))

        async def run():
            owner, task = create_cloud_apps(runtime)
            route = '/api/cloud/v1/capacity'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner), base_url=runtime.settings.origin) as client:
                assert (await client.get(route)).status_code == 401
                response = await client.get(route, headers=owner_headers(runtime))
                assert response.status_code == 200, response.text
                body = response.json()
                assert body['instance_id'] == runtime.instance_id
                assert body['counts'] == dict(active=3, reserved=1, unknown=1, retained=4)
                assert body['disk']['status'] == 'known' and body['disk']['free_bytes'] > 0
                assert body['disk']['logical_bytes'] > 0
                assert body['can_create'] is False and 'environment_capacity' in body['reasons']
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task), base_url='http://' + runtime.settings.task_listen) as client:
                assert (await client.get(route)).status_code == 404
        asyncio.run(run())


def test_unknown_measurement_is_not_zero_and_refuses_allocation(tmp_path):
    limits = ResourceLimits(scan_max_entries=1, scan_cache_seconds=0)
    with open_cloud_runtime(settings(tmp_path, resource_limits=limits)) as runtime:
        usage = runtime.resources.capacity()
        assert usage['disk']['status'] == 'unknown'
        assert usage['disk']['logical_bytes'] is None
        assert usage['disk']['reason'] == 'resource_scan_budget'
        assert usage['can_create'] is False
        with pytest.raises(CloudError, match='resource_disk_unknown'):
            runtime.resources.ensure()


def test_logical_watermark_and_fresh_free_space_checks(tmp_path, monkeypatch):
    from naiyue_test_agent.application.cloud import resource_disk
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        initial = runtime.resources.disk()
        runtime.resources.limits = ResourceLimits(max_logical_bytes=initial.logical_bytes + 2)
        with pytest.raises(CloudError, match='resource_disk_capacity'):
            runtime.resources.ensure(additional_bytes=3)
        runtime.resources.limits = ResourceLimits(min_free_bytes=5)
        monkeypatch.setattr(resource_disk, 'free_bytes', lambda root: 4)
        with pytest.raises(CloudError, match='resource_disk_capacity'):
            runtime.resources.ensure()
        monkeypatch.setattr(resource_disk, 'free_bytes', lambda root: 8)
        runtime.resources.ensure(additional_bytes=3)
        with pytest.raises(CloudError, match='resource_disk_capacity'):
            runtime.resources.ensure(additional_bytes=4)


def test_scan_never_opens_link_targets_or_fifo(tmp_path):
    from naiyue_test_agent.application.cloud.resource_disk import measure
    root = tmp_path / 'managed'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'secret').write_bytes(b'private' * 1000)
    (root / 'link').symlink_to(outside, target_is_directory=True)
    os.mkfifo(root / 'pipe')
    (root / 'file').write_bytes(b'hello')
    measured = measure(root, ResourceLimits())
    assert measured.status == 'known' and measured.entries == 3
    assert measured.logical_bytes == 5 + (root / 'link').lstat().st_size
    assert (outside / 'secret').read_bytes() == b'private' * 1000


def test_scan_rejects_same_device_mount_and_permission_error(tmp_path, monkeypatch):
    from naiyue_test_agent.application.cloud import resource_disk
    root = tmp_path / 'managed'; root.mkdir(); (root / 'child').mkdir()
    original = resource_disk.mount_id
    root_inode = root.stat().st_ino
    def changed(fd):
        return 1 if os.fstat(fd).st_ino == root_inode else 2
    monkeypatch.setattr(resource_disk, 'mount_id', changed)
    assert resource_disk.measure(root, ResourceLimits()).reason == 'resource_scan_mount'
    monkeypatch.setattr(resource_disk, 'mount_id', original)
    def denied(fd):
        raise PermissionError('untrusted detail must not reach the report')
    monkeypatch.setattr(resource_disk, 'mount_id', denied)
    value = resource_disk.measure(root, ResourceLimits())
    assert value.status == 'unknown' and value.reason == 'resource_scan_unavailable'
    assert 'untrusted' not in value.model_dump_json()


@pytest.mark.parametrize('updates', [dict(active_run_id='a' * 32), dict(reclaim_blockers=['required_save']),
    dict(reclaim_blockers=['output_pending']), dict(environment_phase='unknown'),
    dict(control_operation_id=str(uuid4())), dict(artifact_operation_id=str(uuid4())),
    dict(lifecycle='deleting'), dict(last_quiescent_at=None)])
def test_idle_has_explicit_protection_even_without_matching_blocker(tmp_path, updates):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = task_record(str(uuid4()), lifecycle='ready', sandbox_id='one',
                    environment_phase='backend_ready', last_quiescent_at=1.0)
                assert not runtime.lifecycle.idle(task.model_copy(update=updates), 10.0)
        asyncio.run(run())


def test_maintenance_pages_without_starvation_and_preserves_workspace(tmp_path):
    with open_cloud_runtime(config(tmp_path, resource_limits=ResourceLimits(task_batch_size=1))) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                for _ in range(3):
                    task = register(runtime)
                    await runtime.lifecycle.command(task.task_id, command(task))
                    current = runtime.registry.get_task(task.task_id)
                    runtime.replace_task(current.model_copy(update={'last_quiescent_at': 1.0, 'revision': current.revision + 1}),
                        expected_revision=current.revision, expected_generation=current.generation)
                    # Stop first; the retained workspace must survive the periodic path as well.
                    await runtime.lifecycle.command(task.task_id, command(runtime.registry.get_task(task.task_id), 'stop_environment'))
                rows = runtime.lifecycle.repository.task_page('', 1000)
                selected = rows[-1]
                await runtime.lifecycle.command(selected.task_id, command(selected))
                current = runtime.registry.get_task(selected.task_id)
                runtime.replace_task(current.model_copy(update={'last_quiescent_at': 1.0, 'revision': current.revision + 1}),
                    expected_revision=current.revision, expected_generation=current.generation)
                workspace = runtime.settings.data_dir / 'tasks' / selected.task_id / 'workspace'
                (workspace / 'keep').write_text('keep')
                for _ in rows:
                    assert len(await runtime.lifecycle.sweep_idle(now=10000.0)) <= 1
                assert runtime.registry.get_task(selected.task_id).lifecycle == 'stopped'
                assert (workspace / 'keep').read_text() == 'keep'
        asyncio.run(run())


def test_periodic_resource_job_is_owned_stops_and_isolates_safe_failures(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.service.cloud.server import _maintain_resources
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                stopping = asyncio.Event(); calls = []
                async def sweep(**kwargs):
                    calls.append('sweep'); stopping.set()
                    raise CloudError('backend_unavailable')
                monkeypatch.setattr(runtime.lifecycle, 'sweep_idle', sweep)
                await _maintain_resources(runtime, stopping)
                assert calls == ['sweep']
        asyncio.run(run())
    assert 'cloud_resource_maintenance_failed' in caplog.text


def test_scan_timeout_depth_and_symlink_replacement_are_unknown(tmp_path, monkeypatch):
    from naiyue_test_agent.application.cloud import resource_disk
    root = tmp_path / 'managed'; root.mkdir()
    child = root / 'child'; child.mkdir(); (child / 'deeper').mkdir()
    assert resource_disk.measure(root, ResourceLimits(scan_max_depth=1)).reason == 'resource_scan_budget'
    clock = iter([0.0, 2.0])
    with monkeypatch.context() as patch:
        patch.setattr(resource_disk.time, 'monotonic', lambda: next(clock))
        assert resource_disk.measure(root, ResourceLimits(scan_timeout_seconds=1.0)).reason == 'resource_scan_budget'
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'keep').write_text('keep')
    original = resource_disk.open_directory
    def swapped(path, *, dir_fd=None):
        if dir_fd is not None and path == 'child':
            (child / 'deeper').rmdir(); child.rmdir(); child.symlink_to(outside, target_is_directory=True)
        return original(path, dir_fd=dir_fd)
    monkeypatch.setattr(resource_disk, 'open_directory', swapped)
    assert resource_disk.measure(root, ResourceLimits()).status == 'unknown'
    assert (outside / 'keep').read_text() == 'keep'


@pytest.mark.parametrize('value,valid', [(b'pos: 0\nmnt_id:\t42\n', True), (b'pos: 0\n', False), (b'x' * 4097, False)])
def test_linux_mount_identity_requires_bounded_kernel_field(monkeypatch, value, valid):
    import io
    from naiyue_test_agent.application.cloud import resource_disk
    monkeypatch.setattr(resource_disk.sys, 'platform', 'linux')
    monkeypatch.setattr(resource_disk, 'open', lambda *args: io.BytesIO(value), raising=False)
    if valid:
        assert resource_disk.mount_id(123) == 42
    else:
        with pytest.raises(OSError):resource_disk.mount_id(123)


@pytest.mark.parametrize('mode', ['shutdown', 'one_failure', 'new_run'])
def test_idle_shutdown_failure_and_new_run_do_not_kill_unrelated_work(tmp_path, monkeypatch, mode, caplog):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                for _ in range(2):
                    record = register(runtime)
                    await runtime.lifecycle.command(record.task_id, command(record))
                    task = runtime.registry.get_task(record.task_id)
                    runtime.replace_task(task.model_copy(update={'revision': task.revision + 1, 'last_quiescent_at': 1.0}),
                        expected_revision=task.revision, expected_generation=task.generation)
                rows = runtime.lifecycle.repository.task_page('', 1000)
                stopping = asyncio.Event(); entered = asyncio.Event(); release = asyncio.Event()
                original = runtime.lifecycle.command; calls = []
                async def controlled(task_id, value, **kwargs):
                    calls.append(task_id)
                    if len(calls) == 1:
                        if mode == 'one_failure':raise CloudError('template_unavailable')
                        if mode == 'new_run':
                            task = runtime.registry.get_task(task_id)
                            runtime.replace_task(task.model_copy(update={'revision': task.revision + 1, 'active_run_id': 'a' * 32}),
                                expected_revision=task.revision, expected_generation=task.generation)
                        if mode == 'shutdown':
                            entered.set(); await release.wait()
                    return await original(task_id, value, **kwargs)
                monkeypatch.setattr(runtime.lifecycle, 'command', controlled)
                job = asyncio.create_task(runtime.lifecycle.sweep_idle(now=10000.0, stopping=stopping))
                if mode == 'shutdown':
                    await asyncio.wait_for(entered.wait(), 5)
                    stopping.set(); release.set()
                result = await asyncio.wait_for(job, 5)
                assert len(result) == 1 and result[0].status == 'succeeded'
                expected = [rows[0].task_id] if mode == 'shutdown' else [r.task_id for r in rows]
                assert calls == expected
                preserved = rows[1] if mode == 'shutdown' else rows[0]
                assert runtime.registry.get_task(preserved.task_id).sandbox_id == preserved.sandbox_id
                assert len([c for c in backend.calls if c[0] == 'kill']) == 1
        asyncio.run(run())
    if mode == 'one_failure':assert 'cloud_idle_stop_failed' in caplog.text


def test_stop_not_physically_confirmed_stays_occupied_and_capacity_read_is_passive(tmp_path, monkeypatch):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async def not_gone(ident):backend.calls.append(('kill', ident))
            monkeypatch.setattr(backend, 'kill', not_gone)
            async with open_cloud_backend(runtime, backend=backend):
                task = register(runtime)
                await runtime.lifecycle.command(task.task_id, command(task))
                current = runtime.registry.get_task(task.task_id)
                runtime.replace_task(current.model_copy(update={'revision': current.revision + 1, 'last_quiescent_at': 1.0}),
                    expected_revision=current.revision, expected_generation=current.generation)
                result = await runtime.lifecycle.sweep_idle(now=10000.0)
                assert result[0].status == 'unknown' and result[0].error_code == 'sandbox_stop_unconfirmed'
                before = runtime.registry.get_task(task.task_id)
                assert runtime.resources.capacity()['counts'] == dict(active=1, reserved=1, unknown=1, retained=1)
                assert runtime.registry.get_task(task.task_id) == before
                assert backend.sandboxes
        asyncio.run(run())
