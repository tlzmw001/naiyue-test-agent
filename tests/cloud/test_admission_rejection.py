"""Unexecuted admission failures are durable refusals, not uncertain execution."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.transport import RunnerClient
from naiyue_test_agent.contracts.cloud_artifacts import CaptureArtifact
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from cloud_helpers import owner_headers
from lifecycle_helpers import config
from test_runner_kernel import bootstrap, request
from test_runner_control import prepared, client, connect, hello


@pytest.mark.parametrize('operation,code', [('run:start','model_unavailable'),('artifact:capture','artifact_busy')])
def test_receiver_persists_refusal_and_replays_query_after_reopen(tmp_path,operation,code):
    async def run():
        b=bootstrap();root=tmp_path/'root';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        command=request() if operation=='run:start' else CaptureArtifact(command_id=str(uuid4()),artifact_id=str(uuid4()),relative_name='report.txt')
        kind='command' if operation=='run:start' else 'artifact_capture'
        query='query' if operation=='run:start' else 'artifact_query'
        async with open_runner(b,root,state) as kernel:
            if operation=='artifact:capture':kernel.phase='blocked'
            transport=RunnerClient(kernel);transport.send=AsyncMock()
            transport.receive=AsyncMock(side_effect=[SimpleNamespace(kind=kind,command=command),asyncio.CancelledError()])
            with pytest.raises(asyncio.CancelledError):await transport.receiver()
            refusal=transport.send.call_args.args[0]
            assert refusal['kind']=='admission_rejected' and refusal['error_code']==code
            assert refusal['request_digest']==digest(command.model_dump())
            assert kernel.journal.lookup(command.command_id) is None
            assert kernel.journal.latest() is None and kernel.journal.pending()==[]
        async with open_runner(b,root,state) as kernel:
            transport=RunnerClient(kernel);transport.send=AsyncMock()
            transport.receive=AsyncMock(side_effect=[SimpleNamespace(kind=query,command_id=command.command_id),asyncio.CancelledError()])
            with pytest.raises(asyncio.CancelledError):await transport.receiver()
            assert transport.send.call_args.args[0]['request_digest']==refusal['request_digest']
            assert transport.send.call_args.args[0]['error_code']==code
            # Same ID with a different command must never become a fresh refusal.
            changed=command.model_copy(update={'message':'changed'} if operation=='run:start' else {'relative_name':'different.txt'})
            transport.receive=AsyncMock(return_value=SimpleNamespace(kind=kind,command=changed))
            with pytest.raises(CloudError,match='command_id_conflict'):await transport.receiver()
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['run:start','artifact:capture'])
def test_coordinator_refusal_settles_reservation_and_connection_survives(tmp_path,operation):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b)|{'capabilities':['artifacts:capture']});fence=ws.receive_json();fence.pop('kind')
            task=r.registry.get_task(b.task_id)
            value=dict(command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation)
            value.update(dict(message='work',model_id='A') if operation=='run:start' else dict(relative_name='report.txt'))
            path='/api/cloud/v1/tasks/'+b.task_id+('/runs' if operation=='run:start' else '/artifacts')
            response=c.post(path,headers=owner_headers(r),json=value);assert response.status_code<300,response.text
            command=ws.receive_json()['command']
            frame=dict(kind='admission_rejected',command_id=value['command_id'],operation=operation,
                request_digest=digest(command),error_code='runner_busy' if operation=='run:start' else 'artifact_busy',phase='ready',quiescent=True,**fence)
            ws.send_json(frame);ws.send_json(dict(kind='heartbeat',**fence))
            assert ws.receive_json()['kind']=='welcome'
            settled=r.registry.get_command(value['command_id']);task=r.registry.get_task(b.task_id)
            assert settled.status=='failed' and settled.error_code==frame['error_code']
            assert task.active_run_id is None and task.artifact_operation_id is None
            assert task.runner_phase=='ready' and task.reclaim_blockers==[]
            assert task.last_run_id is None
            if operation=='run:start':
                result=r.results.get(task.task_id,command['result_plan']['result_id'])
                assert result.phase=='abandoned' and result.boundary is None
                assert result.plan.result_id not in task.result_ids
            ws.send_json(frame);ws.send_json(dict(kind='heartbeat',**fence))
            assert ws.receive_json()['kind']=='welcome'
            assert c.post(path,headers=owner_headers(r),json=value).json()['status']=='failed'


@pytest.mark.parametrize('error', [CloudError('command_id_conflict'), OSError('disk unavailable')])
def test_receiver_does_not_reclassify_identity_or_io_failure(tmp_path,error):
    async def run():
        b=bootstrap();root=tmp_path/'root';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        async with open_runner(b,root,state) as kernel:
            command=request();kernel.accept=AsyncMock(side_effect=error)
            transport=RunnerClient(kernel);transport.send=AsyncMock()
            transport.receive=AsyncMock(return_value=SimpleNamespace(kind='command',command=command))
            with pytest.raises(type(error)) as caught:await transport.receiver()
            assert caught.value is error and transport.send.call_count==0
            assert kernel.journal.rejection(command.command_id) is None
    asyncio.run(run())


def test_refusal_survives_lost_send_and_storage_failure_never_claims_rejection(tmp_path,monkeypatch):
    async def run():
        b=bootstrap();root=tmp_path/'root';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        async with open_runner(b,root,state) as kernel:
            command=request();transport=RunnerClient(kernel)
            transport.receive=AsyncMock(return_value=SimpleNamespace(kind='command',command=command))
            transport.send=AsyncMock(side_effect=OSError('lost ACK'))
            with pytest.raises(OSError):await transport.receiver()
            assert kernel.journal.rejection(command.command_id)['error_code']=='model_unavailable'
            transport.send=AsyncMock();transport.receive=AsyncMock(side_effect=[SimpleNamespace(kind='query',command_id=command.command_id),asyncio.CancelledError()])
            with pytest.raises(asyncio.CancelledError):await transport.receiver()
            assert transport.send.call_args.args[0]['kind']=='admission_rejected'
            def fail(*args):raise OSError('necessary save failed')
            monkeypatch.setattr(kernel.journal,'reject',fail)
            transport.send.reset_mock();transport.receive=AsyncMock(return_value=SimpleNamespace(kind='command',command=request()))
            with pytest.raises(OSError):await transport.receiver()
            assert transport.send.call_count==0
    asyncio.run(run())


def test_rejected_next_run_restores_prior_pending_result(tmp_path):
    from test_results import finish,run_body
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));welcome=ws.receive_json();fence={k:v for k,v in welcome.items() if k!='kind'}
            previous,_=finish(r,b,c,ws,run_body(r.registry.get_task(b.task_id)))
            rid=previous['result_plan']['result_id']
            body=run_body(r.registry.get_task(b.task_id))
            assert c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=body).status_code==202
            command=ws.receive_json()['command']
            ws.send_json(dict(kind='admission_rejected',command_id=command['command_id'],operation='run:start',
                request_digest=digest(command),error_code='runner_busy',phase='ready',quiescent=True,**fence))
            ws.send_json(dict(kind='heartbeat',**fence));assert ws.receive_json()['kind']=='welcome'
            task=r.registry.get_task(b.task_id)
            assert task.last_run_id==previous['run_id'] and task.pending_result_id==rid
            assert task.reclaim_blockers==['result_pending']
            assert r.results.get(b.task_id,rid).phase=='pending'


def test_runner_keeps_prior_plan_until_replacing_run_is_really_admitted(tmp_path,monkeypatch):
    from test_runner_result_retirement import fixture,command,remote
    from naiyue_test_agent.contracts.cloud_results import ResultRecord
    worker,transport=fixture(tmp_path);old=command(worker);worker.admit(old)
    next_run=uuid4().hex
    worker.kernel.journal.admitted_run=lambda run_id:False
    remote(monkeypatch,ResultRecord(plan=old.result_plan,phase='superseded',superseded_by=next_run))
    asyncio.run(worker.publish(transport))
    assert worker.path(old.result_plan.result_id).exists()
    worker.kernel.journal.admitted_run=lambda run_id:run_id==next_run
    asyncio.run(worker.publish(transport))
    assert not worker.path(old.result_plan.result_id).exists()


def test_temporary_busy_refusal_does_not_require_recovery_after_ready_heartbeat(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));welcome=ws.receive_json();fence={k:v for k,v in welcome.items() if k!='kind'}
            task=r.registry.get_task(b.task_id);value=dict(command_id=str(uuid4()),expected_revision=task.revision,
                expected_generation=task.generation,message='work',model_id='A')
            c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=value)
            command=ws.receive_json()['command']
            ws.send_json(dict(kind='admission_rejected',command_id=command['command_id'],operation='run:start',request_digest=digest(command),
                error_code='runner_busy',phase='blocked',quiescent=False,**fence))
            ws.send_json(dict(kind='heartbeat',phase='ready',quiescent=True,run_id=None,**fence))
            assert ws.receive_json()['kind']=='welcome'
            current=r.registry.get_task(b.task_id)
            assert current.runner_phase=='ready' and current.reclaim_blockers==[]
