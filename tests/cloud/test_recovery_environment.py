"""Explicit fencing retains unknown work; an uncertain stop never starts a new writer."""
import asyncio
from uuid import uuid4
import pytest
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from lifecycle_helpers import Backend,command,config,register


def test_acknowledged_stop_then_start_fences_old_container_but_preserves_unknown(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def scenario():
            backend=Backend()
            async with open_cloud_backend(r,backend=backend):
                task=register(r)
                await r.lifecycle.command(task.task_id,command(task))
                old=r.registry.get_task(task.task_id)
                unknown=old.model_copy(update={'revision':old.revision+1,'active_run_id':uuid4().hex,
                    'reclaim_blockers':['active_run','unknown'],'runner_phase':'blocked'})
                r.replace_task(unknown,expected_revision=old.revision,expected_generation=old.generation)
                with pytest.raises(CloudError,match='task_busy'):
                    await r.lifecycle.command(task.task_id,command(unknown,'stop_environment'))
                stopped=await r.lifecycle.command(task.task_id,command(unknown,'stop_environment',acknowledge_unknown=True))
                assert stopped.status=='succeeded' and old.sandbox_id not in backend.sandboxes
                absent=r.registry.get_task(task.task_id)
                assert absent.active_run_id==unknown.active_run_id and 'unknown' in absent.reclaim_blockers
                with pytest.raises(CloudError,match='task_busy'):
                    await r.lifecycle.command(task.task_id,command(absent))
                started=await r.lifecycle.command(task.task_id,command(absent,acknowledge_unknown=True))
                assert started.status=='succeeded'
                new=r.registry.get_task(task.task_id)
                assert new.generation==old.generation+1 and new.sandbox_id!=old.sandbox_id
                assert new.active_run_id==unknown.active_run_id and 'unknown' in new.reclaim_blockers
        asyncio.run(scenario())


def test_unconfirmed_backend_stop_keeps_old_generation_and_blocks_acknowledged_start(tmp_path,monkeypatch):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def scenario():
            backend=Backend()
            async with open_cloud_backend(r,backend=backend):
                task=register(r);await r.lifecycle.command(task.task_id,command(task))
                old=r.registry.get_task(task.task_id)
                async def still_running(sandbox_id):backend.calls.append(('ignored_kill',sandbox_id))
                monkeypatch.setattr(backend,'kill',still_running)
                result=await r.lifecycle.command(task.task_id,command(old,'stop_environment',acknowledge_unknown=True))
                assert result.status=='unknown'
                current=r.registry.get_task(task.task_id)
                with pytest.raises(CloudError,match='task_busy'):
                    await r.lifecycle.command(task.task_id,command(current,acknowledge_unknown=True))
                assert current.generation==old.generation and current.sandbox_id==old.sandbox_id
        asyncio.run(scenario())


def test_idle_reclaim_cannot_use_unknown_acknowledgement(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def scenario():
            async with open_cloud_backend(r,backend=Backend()):
                task=register(r)
                with pytest.raises(CloudError,match='invalid_environment_acknowledgement'):
                    await r.lifecycle.command(task.task_id,command(task,'stop_environment',acknowledge_unknown=True),idle_cutoff=0)
        asyncio.run(scenario())
