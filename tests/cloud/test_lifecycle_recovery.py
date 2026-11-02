import asyncio

import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from lifecycle_helpers import Backend, command, config, register, template


def test_refresh_missing_container_then_rebuild_original_directories(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                task = register(runtime)
                await runtime.lifecycle.command(task.task_id, command(task))
                old = runtime.registry.get_task(task.task_id)
                backend.sandboxes.clear()  # independently confirmed external removal
                receipt = await runtime.lifecycle.command(task.task_id, command(old, 'refresh'))
                assert receipt.status == 'succeeded'
                gone = runtime.registry.get_task(task.task_id)
                assert gone.lifecycle == 'stopped' and gone.sandbox_id is None
                await runtime.lifecycle.command(task.task_id, command(gone))
                assert runtime.registry.get_task(task.task_id).generation == old.generation + 1
        asyncio.run(run())


def test_old_task_uses_frozen_template_when_default_changes(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend):
                task = register(runtime)
                await runtime.lifecycle.command(task.task_id, command(task))
                current = runtime.registry.get_task(task.task_id)
                runtime.settings.templates['python-tests'] = template(image_id='sha256:' + '9' * 64)
                await runtime.lifecycle.command(task.task_id, command(current, 'stop_environment'))
                stopped = runtime.registry.get_task(task.task_id)
                await runtime.lifecycle.command(task.task_id, command(stopped))
                live = runtime.registry.get_task(task.task_id)
                assert backend.sandboxes[live.sandbox_id].image_id == 'sha256:' + '1' * 64
                runtime.settings.templates.clear()
                await runtime.lifecycle.command(task.task_id, command(live, 'stop_environment'))
        asyncio.run(run())


def test_stop_remains_available_when_retained_budget_is_exceeded(tmp_path):
    with open_cloud_runtime(config(tmp_path, max_retained_environments=1)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                first = register(runtime)
                await runtime.lifecycle.command(first.task_id, command(first))
                register(runtime)
                current = runtime.registry.get_task(first.task_id)
                assert (await runtime.lifecycle.command(first.task_id, command(current, 'stop_environment'))).status == 'succeeded'
        asyncio.run(run())


def test_reconcile_racing_completion_returns_durable_final_receipt(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            entered, complete, snapshot, release = (asyncio.Event() for _ in range(4))
            class WaitingBackend(Backend):
                async def create(self, *args):
                    entered.set()
                    await complete.wait()
                    return await super().create(*args)
            backend = WaitingBackend()
            async with open_cloud_backend(runtime, backend=backend) as service:
                task = register(runtime)
                request = command(task)
                starting = asyncio.create_task(service.command(task.task_id, request))
                await entered.wait()
                local = service.local
                reconciling = None
                async def paused_read(action):
                    result = await local(action)
                    if asyncio.current_task() is reconciling:
                        snapshot.set()
                        await release.wait()
                    return result
                service.local = paused_read
                reconciling = asyncio.create_task(service.reconcile(request['command_id']))
                await snapshot.wait()
                complete.set()
                finished = await starting
                assert finished.status == 'succeeded'
                release.set()
                assert await reconciling == finished
                assert len([c for c in backend.calls if c[0] == 'create']) == 1
        asyncio.run(run())


def test_stop_ack_loss_blocks_rebuild_until_missing_is_reconciled(tmp_path):
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Backend()
            async with open_cloud_backend(runtime, backend=backend) as service:
                task = register(runtime)
                await service.command(task.task_id, command(task))
                live = runtime.registry.get_task(task.task_id)
                backend.lose_stop_ack = True
                stopping = command(live, 'stop_environment')
                assert (await service.command(task.task_id, stopping)).status == 'unknown'
                unknown = runtime.registry.get_task(task.task_id)
                with pytest.raises(CloudError, match='task_busy'):
                    await service.command(task.task_id, command(unknown))
                assert (await service.reconcile(stopping['command_id'])).status == 'succeeded'
                stopped = runtime.registry.get_task(task.task_id)
                await service.command(task.task_id, command(stopped))
                assert runtime.registry.get_task(task.task_id).generation == live.generation + 1
                assert len([c for c in backend.calls if c[0] == 'kill']) == 1
        asyncio.run(run())


def test_concurrent_task_admission_reserves_one_capacity_slot(tmp_path):
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    with open_cloud_runtime(config(tmp_path, max_active_environments=1)) as runtime:
        async def run():
            entered, release = asyncio.Event(), asyncio.Event()
            class WaitingBackend(Backend):
                async def create(self, *args):
                    entered.set()
                    await release.wait()
                    return await super().create(*args)
            backend = WaitingBackend()
            async with open_cloud_backend(runtime, backend=backend) as service:
                first, second = register(runtime), register(runtime)
                producer = asyncio.create_task(service.command(first.task_id, command(first)))
                await entered.wait()
                try:
                    with pytest.raises(CloudError, match='environment_capacity'):
                        await service.command(second.task_id, command(second))
                finally:
                    release.set()
                assert (await producer).status == 'succeeded'
                assert runtime.registry.get_task(second.task_id).revision == second.revision
                assert len([c for c in backend.calls if c[0] == 'create']) == 1
        asyncio.run(run())
