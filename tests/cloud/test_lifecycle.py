import asyncio
from uuid import uuid4

import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from lifecycle_helpers import Backend, command, config, register, template


def test_start_stop_recreate_keeps_files_and_advances_generation(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                record = register(runtime)
                request = command(record)
                first = await runtime.lifecycle.command(record.task_id, request)
                assert first.status == 'succeeded'
                current = runtime.registry.get_task(record.task_id)
                assert current.lifecycle == 'starting' and current.environment_phase == 'backend_ready'
                assert await runtime.lifecycle.command(record.task_id, request) == first
                workspace = runtime.settings.data_dir / 'tasks' / record.task_id / 'workspace'
                (workspace / 'result.txt').write_text('keep me')
                (workspace / 'result.txt').chmod(0o600)
                await runtime.lifecycle.command(record.task_id, command(current, 'stop_environment'))
                stopped = runtime.registry.get_task(record.task_id)
                assert stopped.lifecycle == 'stopped' and stopped.sandbox_id is None
                await runtime.lifecycle.command(record.task_id, command(stopped))
                restarted = runtime.registry.get_task(record.task_id)
                assert restarted.generation == record.generation + 1
                assert restarted.sandbox_id != current.sandbox_id
                assert (workspace / 'result.txt').read_text() == 'keep me'
            assert backend.closed
        asyncio.run(run())


def test_create_ack_loss_reopen_adopts_one_container_without_recreate(tmp_path):
    backend = Backend()
    backend.lose_create_ack = True
    settings = config(tmp_path)
    with open_cloud_runtime(settings) as runtime:
        async def first_run():
            async with open_cloud_backend(runtime, backend=backend):
                task = register(runtime)
                request = command(task)
                receipt = await runtime.lifecycle.command(task.task_id, request)
                assert receipt.status == 'unknown'
                return task, request
        task, request = asyncio.run(first_run())
    with open_cloud_runtime(settings) as runtime:
        async def second_run():
            async with open_cloud_backend(runtime, backend=backend):
                original = await runtime.lifecycle.command(task.task_id, request)
                assert original.status == 'unknown'
                reconciled = await runtime.lifecycle.reconcile(request['command_id'])
                assert reconciled.status == 'succeeded'
                assert runtime.registry.get_task(task.task_id).environment_phase == 'backend_ready'
                assert len([c for c in backend.calls if c[0] == 'create']) == 1
        asyncio.run(second_run())


def test_unknown_keeps_capacity_and_zero_or_multiple_matches_do_not_create(tmp_path):
    with open_cloud_runtime(config(tmp_path, max_active_environments=1)) as runtime:
        async def run():
            backend = Backend()
            backend.lose_create_ack = True
            async with open_cloud_backend(runtime, backend=backend):
                first, second = register(runtime), register(runtime)
                request = command(first)
                assert (await runtime.lifecycle.command(first.task_id, request)).status == 'unknown'
                with pytest.raises(CloudError, match='environment_capacity'):
                    await runtime.lifecycle.command(second.task_id, command(second))
                saved = next(iter(backend.sandboxes.values()))
                backend.sandboxes.clear()
                assert (await runtime.lifecycle.reconcile(request['command_id'])).status == 'unknown'
                backend.sandboxes['one'] = saved
                backend.sandboxes['two'] = saved.model_copy(update={'sandbox_id': str(uuid4())})
                assert (await runtime.lifecycle.reconcile(request['command_id'])).status == 'unknown'
                assert len([c for c in backend.calls if c[0] == 'create']) == 1
        asyncio.run(run())


@pytest.mark.parametrize('blocker', ['pending_approval', 'required_save', 'output_pending', 'unknown', 'background_command'])
def test_stop_and_idle_sweep_preserve_required_work(tmp_path, blocker):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                record = register(runtime)
                await runtime.lifecycle.command(record.task_id, command(record))
                current = runtime.registry.get_task(record.task_id)
                blocked = current.model_copy(update={'revision': current.revision + 1,
                    'last_quiescent_at': 1.0, 'reclaim_blockers': [blocker]})
                runtime.replace_task(blocked, expected_revision=current.revision, expected_generation=current.generation)
                with pytest.raises(CloudError, match='task_busy'):
                    await runtime.lifecycle.command(record.task_id, command(blocked, 'stop_environment'))
                assert await runtime.lifecycle.sweep_idle(now=10000.0) == []
                assert not any(c[0] == 'kill' for c in backend.calls)
        asyncio.run(run())


def test_digest_conflict_stale_cas_and_backend_identity_reject_before_dispatch(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                record = register(runtime)
                request = command(record)
                await runtime.lifecycle.command(record.task_id, request)
                with pytest.raises(CloudError, match='command_id_conflict'):
                    await runtime.lifecycle.command(record.task_id, request | {'kind': 'stop_environment'})
                with pytest.raises(CloudError, match='task_revision_conflict'):
                    await runtime.lifecycle.command(record.task_id, command(record))
                other = register(runtime)
                backend.backend_id = 'different-daemon'
                with pytest.raises(CloudError, match='backend_identity_mismatch'):
                    await runtime.lifecycle.command(other.task_id, command(other))
                assert len([c for c in backend.calls if c[0] == 'create']) == 1
        asyncio.run(run())


def test_idle_requires_positive_quiescent_boundary_and_double_checks_revision(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                record = register(runtime)
                await runtime.lifecycle.command(record.task_id, command(record))
                assert await runtime.lifecycle.sweep_idle(now=10000.0) == []
                current = runtime.registry.get_task(record.task_id)
                idle = current.model_copy(update={'revision': current.revision + 1, 'last_quiescent_at': 1.0})
                runtime.replace_task(idle, expected_revision=current.revision, expected_generation=current.generation)
                stopped = await runtime.lifecycle.sweep_idle(now=10000.0)
                assert len(stopped) == 1 and stopped[0].status == 'succeeded'
                assert runtime.registry.get_task(record.task_id).lifecycle == 'stopped'
        asyncio.run(run())
