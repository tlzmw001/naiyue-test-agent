import asyncio
import os
from uuid import uuid4

import pytest

from lifecycle_helpers import Backend, command, config, register
from test_task_delete import request
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_resources import ResourceLimits


@pytest.mark.parametrize('fault', ['offline', 'still_exists', 'wrong_identity', 'multiple'])
def test_unknown_delete_never_cleans_without_physical_proof(tmp_path, fault):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend(); backend.lose_create_ack = True
            async with open_cloud_backend(runtime, backend=backend):
                task = register(runtime)
                started = await runtime.lifecycle.command(task.task_id, command(task))
                assert started.status == 'unknown'
                parent = runtime.settings.data_dir / 'tasks' / task.task_id
                if fault == 'offline': backend.offline = True
                if fault == 'still_exists':
                    async def keep(ident):backend.calls.append(('kill', ident))
                    backend.kill = keep
                if fault == 'wrong_identity':
                    key, fact = next(iter(backend.sandboxes.items()))
                    backend.sandboxes[key] = fact.model_copy(update={'image_id':'sha256:' + 'f' * 64})
                if fault == 'multiple':
                    fact = next(iter(backend.sandboxes.values()))
                    backend.sandboxes['another'] = fact.model_copy(update={'sandbox_id':'another'})
                receipt = await runtime.deletions.command(task.task_id, request(runtime, task.task_id, acknowledge_unknown=True))
                assert receipt.status == 'unknown' and receipt.result['phase'] == 'reserved'
                assert parent.exists() and runtime.resources.capacity()['counts']['retained'] == 1
                assert runtime.registry.get_task(task.task_id).lifecycle == 'deleting'
                if fault in ('wrong_identity', 'multiple'):
                    assert not any(c[0] == 'kill' for c in backend.calls)
        asyncio.run(run())


def test_unknown_create_without_id_is_found_and_deleted(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend(); backend.lose_create_ack = True
            async with open_cloud_backend(runtime, backend=backend):
                task = register(runtime)
                assert (await runtime.lifecycle.command(task.task_id, command(task))).status == 'unknown'
                assert runtime.registry.get_task(task.task_id).sandbox_id is None and backend.sandboxes
                receipt = await runtime.deletions.command(task.task_id, request(runtime, task.task_id, acknowledge_unknown=True))
                assert receipt.status == 'succeeded' and not backend.sandboxes
                assert len([c for c in backend.calls if c[0] == 'kill']) == 1
        asyncio.run(run())


def test_partial_cleanup_restarts_same_delete_and_scrubs_command_bodies(tmp_path, monkeypatch):
    settings = config(tmp_path); backend = Backend()
    from naiyue_test_agent.application.cloud import deletions
    cleanup = deletions.remove_task_data
    with open_cloud_runtime(settings) as runtime:
        async def first():
            async with open_cloud_backend(runtime, backend=backend):
                task = register(runtime)
                parent = settings.data_dir / 'tasks' / task.task_id; parent.mkdir(parents=True)
                (parent / 'first').write_text('first'); (parent / 'second').write_text('second')
                def interrupted(*args):
                    (parent / 'first').unlink()
                    raise PermissionError('private file detail')
                monkeypatch.setattr(deletions, 'remove_task_data', interrupted)
                original = request(runtime, task.task_id)
                receipt = await runtime.deletions.command(task.task_id, original)
                assert receipt.status == 'unknown' and receipt.result['phase'] == 'cleaning'
                assert (parent / 'second').exists() and runtime.resources.capacity()['counts']['retained'] == 1
                return task, original
        task, original = asyncio.run(first())
    monkeypatch.setattr(deletions, 'remove_task_data', cleanup)
    with open_cloud_runtime(settings) as runtime:
        async def second():
            async with open_cloud_backend(runtime, backend=backend):
                assert (await runtime.deletions.command(task.task_id, original)).status == 'unknown'
                receipt = await runtime.deletions.reconcile(original['command_id'])
                assert receipt.status == 'succeeded'
                assert not (settings.data_dir / 'tasks' / task.task_id).exists()
                assert runtime.resources.capacity()['counts']['retained'] == 0
        asyncio.run(second())


def test_cleanup_symlink_fifo_mount_and_budget_are_scoped(tmp_path, monkeypatch):
    from naiyue_test_agent.application.cloud import task_cleanup
    root = tmp_path / 'root'; root.mkdir(); tasks = root / 'tasks'; tasks.mkdir()
    ident = uuid4().hex; parent = tasks / ident; parent.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'keep').write_text('keep')
    (parent / 'link').symlink_to(outside); os.mkfifo(parent / 'pipe')
    task_cleanup.remove_task_data(root, ident, ResourceLimits())
    assert not parent.exists() and (outside / 'keep').read_text() == 'keep'
    parent.mkdir(); (parent / 'deep').mkdir()
    with pytest.raises(CloudError, match='cleanup_budget'):
        task_cleanup.remove_task_data(root, ident, ResourceLimits(scan_max_entries=1))
    inode = parent.stat().st_ino
    monkeypatch.setattr(task_cleanup, 'mount_id', lambda fd: 2 if os.fstat(fd).st_ino == inode else 1)
    with pytest.raises(CloudError, match='task_delete_mount'):
        task_cleanup.remove_task_data(root, ident, ResourceLimits())
    assert parent.exists()


def test_delete_drains_late_create_before_find_and_cleans_after_it(tmp_path, monkeypatch):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def race():
            backend = Backend(); entered = asyncio.Event(); release = asyncio.Event(); original = backend.create
            async def delayed(*args):
                entered.set(); await release.wait(); return await original(*args)
            backend.create = delayed
            async with open_cloud_backend(runtime, backend=backend):
                reserved = asyncio.Event(); reserve = runtime.deletions.reserve; loop = asyncio.get_running_loop()
                def admission(*args):
                    result = reserve(*args); loop.call_soon_threadsafe(reserved.set); return result
                monkeypatch.setattr(runtime.deletions, 'reserve', admission)
                task = register(runtime)
                starting = asyncio.create_task(runtime.lifecycle.command(task.task_id, command(task)))
                await asyncio.wait_for(entered.wait(), 5)
                # A dispatched, unacknowledged create is an uncertain boundary even before its timeout.
                value = request(runtime, task.task_id, acknowledge_unknown=True)
                deleting = asyncio.create_task(runtime.deletions.command(task.task_id, value))
                await asyncio.wait_for(reserved.wait(), 5)
                assert runtime.registry.get_task(task.task_id).lifecycle == 'deleting'
                assert not deleting.done() and not any(c[0] == 'kill' for c in backend.calls)
                release.set()
                await asyncio.gather(starting, return_exceptions=True)
                assert (await asyncio.wait_for(deleting, 5)).status == 'succeeded'
                assert not backend.sandboxes and len([c for c in backend.calls if c[0]=='create']) == 1
        asyncio.run(race())
