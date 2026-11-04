"""Maintenance outages stay local; reservations avoid unrelated task decoding."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from naiyue_test_agent.service.cloud import server
from naiyue_test_agent.contracts.cloud_tasks import CloudTask, CommandReceipt, CloudError, CreateProject
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository
from cloud_helpers import settings, task_record


@pytest.mark.parametrize('kind',['inputs','resources'])
def test_unexpected_maintenance_failure_retries_and_cancellation_propagates(monkeypatch,kind):
    async def run():
        stopping=asyncio.Event();calls=[]
        try:CloudTask.model_validate_json('{}')
        except ValidationError as error:invalid=error
        async def sweep():
            calls.append('sweep')
            if len(calls)==1:raise invalid
            stopping.set()
        async def work(runtime,fn):return await fn()
        async def idle(**kwargs):pass
        async def disk(**kwargs):pass
        runtime=SimpleNamespace(inputs=SimpleNamespace(sweep_expired=sweep),results=SimpleNamespace(retention=SimpleNamespace(sweep=sweep)),
            lifecycle=SimpleNamespace(sweep_idle=idle),resources=SimpleNamespace(disk=disk),
            settings=SimpleNamespace(input_limits=SimpleNamespace(sweep_interval_seconds=0),resource_limits=SimpleNamespace(maintenance_interval_seconds=0)))
        monkeypatch.setattr(server,'cloud_work',work)
        method=getattr(server,'_maintain_'+kind)
        await method(runtime,stopping)
        assert calls==['sweep','sweep']
        async def cancelled():raise asyncio.CancelledError()
        runtime.inputs.sweep_expired=runtime.results.retention.sweep=cancelled
        with pytest.raises(asyncio.CancelledError):await method(runtime,asyncio.Event())
    asyncio.run(run())


@pytest.mark.parametrize('operation',['run:start','artifact:capture','environment:start'])
def test_reserve_never_deserializes_unrelated_task_rows(tmp_path,monkeypatch,operation):
    with open_cloud_runtime(settings(tmp_path)) as r:
        project=r.commands.create_project(CreateProject(command_id=str(uuid4()),name='capacity'))
        task=task_record(project.resource_id);r.registry.add_task(task)
        for _ in range(8):r.registry.add_task(task_record(task.project_id,lifecycle='deleted'))
        previous=task;candidate=task.model_copy(update={'revision':task.revision+1})
        receipt=CommandReceipt(command_id=str(uuid4()),resource_id=task.task_id,operation=operation,expected_generation=1,
            request_digest='a'*64,status='running',http_status=202,result={},created_at=1.0,updated_at=1.0)
        def forbidden(*args,**kwargs):raise AssertionError('unrelated task decoding on reservation')
        monkeypatch.setattr(CloudTask,'model_validate_json',forbidden)
        saved,fresh=LifecycleRepository(r.database).reserve(previous,candidate,receipt,r.settings)
        assert fresh and saved==receipt
