"""Slow durable deletion has its own bounded budget and original-ID recovery."""
import asyncio
import pytest
from lifecycle_helpers import config, Backend, register
from test_task_delete import request
from naiyue_test_agent.contracts.cloud_resources import ResourceLimits
from naiyue_test_agent.application.cloud import task_cleanup
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend


def test_durable_cleanup_does_not_use_fast_scan_deadline(tmp_path,monkeypatch):
    tid='a'*32; root=tmp_path/'tasks'/tid;root.mkdir(parents=True)
    for n in range(4):(root/str(n)).write_text('data')
    clock=[0.0];original=task_cleanup.os.fsync
    def sync(fd):
        original(fd);clock[0]+=0.8
    monkeypatch.setattr(task_cleanup.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(task_cleanup.os,'fsync',sync)
    task_cleanup.remove_task_data(tmp_path,tid,ResourceLimits())
    assert clock[0]>2 and not root.exists()


def test_cleanup_budget_keeps_original_operation_resumable(tmp_path,monkeypatch):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime,backend=Backend()):
                task=register(runtime);root=runtime.settings.data_dir/'tasks'/task.task_id;root.mkdir(parents=True)
                for n in range(4):(root/str(n)).write_text('data')
                # Patch only this module's time object, never asyncio's global clock.
                from types import SimpleNamespace
                clock=[0.0];original=task_cleanup.os.fsync
                def sync(fd):original(fd);clock[0]+=20
                with monkeypatch.context() as patch:
                    patch.setattr(task_cleanup,'time',SimpleNamespace(monotonic=lambda:clock[0]))
                    patch.setattr(task_cleanup.os,'fsync',sync)
                    body=request(runtime,task.task_id)
                    result=await runtime.deletions.command(task.task_id,body)
                assert result.status=='unknown' and result.error_code=='task_delete_cleanup_budget'
                assert runtime.registry.get_task(task.task_id).lifecycle=='deleting'
                result=await runtime.deletions.reconcile(body['command_id'])
                assert result.status=='succeeded' and not root.exists()
                assert result.command_id==body['command_id']
        asyncio.run(run())


@pytest.mark.parametrize('value',[0,-1,3601])
def test_cleanup_budget_is_validated(value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):ResourceLimits(cleanup_timeout_seconds=value)


def test_cleanup_budget_roundtrips_independently():
    limits=ResourceLimits(cleanup_timeout_seconds=120,scan_timeout_seconds=1)
    assert ResourceLimits.model_validate_json(limits.model_dump_json())==limits
    assert limits.scan_timeout_seconds==1
