"""Confirmed publication retires only its own duplicate Runner data, without replay."""
import asyncio
import hashlib
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from naiyue_test_agent.cloud_runner import results as module
from naiyue_test_agent.cloud_runner.results import RunnerResults
from naiyue_test_agent.cloud_runner.state import read_json, write_json
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
from naiyue_test_agent.contracts.cloud_results import ResultBoundary, ResultBundle, ResultFile, ResultPlan, ResultRecord
from naiyue_test_agent.contracts.cloud_runner import RunCommand
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from test_runner_kernel import bootstrap


def fixture(tmp_path):
    b=bootstrap();state=tmp_path/'state';state.mkdir(mode=0o700)
    kernel=SimpleNamespace(bootstrap=b,journal=SimpleNamespace(lookup=lambda _:dict(status='completed',terminal_seq=3)),
        session_quiet=lambda:True,phase='ready',last_run_id=None,recovery_info=None)
    worker=RunnerResults(kernel,tmp_path/'workspace',state)
    async def send(_):pass
    client=SimpleNamespace(bootstrap=b,http=object(),send=send)
    return worker,client


def command(worker):
    b=worker.kernel.bootstrap;cid=str(uuid4());run=uuid4().hex
    plan=ResultPlan(result_id=str(uuid4()),source_command_id=cid,source_run_id=run,task_id=b.task_id,
        project_id=b.project_id,input_id=b.input_id,source_generation=b.generation,output_directory='.nyt-output')
    return RunCommand(command_id=cid,run_id=run,message='next',model_id='A',result_plan=plan)


def spool(worker,cmd):
    worker.admit(cmd);plan=cmd.result_plan;root=worker.spools/plan.result_id
    root.mkdir(mode=0o700);files=root/'files';files.mkdir(mode=0o700);items=[]
    for role in ('manifest','patch'):
        aid=str(uuid4());raw=role.encode();path=files/aid;path.write_bytes(raw);path.chmod(0o600)
        items.append(ResultFile(role=role,artifact=ArtifactRecord(command_id=plan.result_id,artifact_id=aid,
            relative_name=role,task_id=plan.task_id,project_id=plan.project_id,input_id=plan.input_id,
            generation=1,captured_at=1,size=len(raw),sha256=hashlib.sha256(raw).hexdigest())))
    boundary=ResultBoundary(kind='run',epoch=worker.kernel.bootstrap.state_epoch,seq=3,outcome='completed')
    bundle=ResultBundle(plan=plan,boundary=boundary,generation=1,captured_at=1,files=items)
    record=ResultRecord(plan=plan,phase='available',boundary=boundary,bundle=bundle,generation=1)
    write_json(root/'result.json',record.model_dump(mode='json'))
    return record


def remote(monkeypatch,record):
    calls=[]
    async def request(http,b,method,path,**kwargs):
        calls.append((method,path))
        assert method=='GET', 'retirement must reconcile without recapturing or posting'
        return record.model_dump(mode='json')
    def forbidden(*args,**kwargs):raise AssertionError('confirmed spool must not rescan workspace')
    monkeypatch.setattr(module,'task_request',request);monkeypatch.setattr(module,'freeze_result',forbidden)
    return calls


def test_65_old_completed_plans_do_not_exhaust_pending_budget(tmp_path):
    worker,_=fixture(tmp_path)
    for _ in range(65):
        cmd=command(worker)
        write_json(worker.path(cmd.result_plan.result_id),dict(plan=cmd.result_plan.model_dump(mode='json'),done=True,started_attempt=None))
    fresh=command(worker);worker.admit(fresh)
    assert [v['plan']['result_id'] for v in worker.pending()]==[fresh.result_plan.result_id]
    assert list(worker.directory.iterdir())==[worker.path(fresh.result_plan.result_id)]


def test_admission_counts_done_plans_without_competing_with_publisher_cleanup(tmp_path,monkeypatch):
    worker,_=fixture(tmp_path);cmd=command(worker);worker.admit(cmd)
    local=read_json(worker.path(cmd.result_plan.result_id));local['done']=True
    write_json(worker.path(cmd.result_plan.result_id),local)
    def forbidden(*args):raise AssertionError('receiver admission must not race publisher deletion')
    monkeypatch.setattr(worker,'retire',forbidden)
    fresh=command(worker);worker.admit(fresh)
    assert worker.path(fresh.result_plan.result_id).is_file() and worker.path(cmd.result_plan.result_id).is_file()


def test_plan_disappearing_after_inventory_is_normal_cleanup_race(tmp_path,monkeypatch):
    worker,_=fixture(tmp_path);cmd=command(worker);worker.admit(cmd);path=worker.path(cmd.result_plan.result_id)
    original=module.read_json
    def concurrent_retirement(candidate,*args):
        if candidate==path and path.exists():path.unlink()
        return original(candidate,*args)
    monkeypatch.setattr(module,'read_json',concurrent_retirement)
    assert worker.pending()==[]


def test_atomic_plan_write_temporary_is_not_a_second_pending_result(tmp_path):
    worker,_=fixture(tmp_path);cmd=command(worker)
    temporary=worker.path(cmd.result_plan.result_id).with_suffix('.json.next')
    temporary.write_bytes(b'not published');temporary.chmod(0o600)
    fresh=command(worker);worker.admit(fresh)
    assert [v['plan']['result_id'] for v in worker.pending()]==[fresh.result_plan.result_id]


@pytest.mark.parametrize('phase',['available','abandoned','superseded','deleted'])
def test_confirmed_retirement_removes_spool_then_plan_only(tmp_path,monkeypatch,phase):
    worker,client=fixture(tmp_path);cmd=command(worker);record=spool(worker,cmd)
    other=command(worker);other_record=spool(worker,other)
    cache=worker.spools.parent/'result-baseline';cache.mkdir();(cache/'keep').write_text('baseline')
    calls=remote(monkeypatch,record.model_copy(update={'phase':phase}))
    # Keep the second result unknown, and therefore outside the current retirement.
    path=worker.path(other.result_plan.result_id)
    original=worker.pending
    monkeypatch.setattr(worker,'pending',lambda:[v for v in original() if v['plan']['result_id']==record.plan.result_id])
    asyncio.run(worker.publish(client))
    assert calls and not (worker.spools/record.plan.result_id).exists()
    assert not worker.path(record.plan.result_id).exists()
    assert (worker.spools/other_record.plan.result_id/'result.json').is_file() and path.is_file()
    assert (cache/'keep').read_text()=='baseline'


def test_unknown_result_keeps_spool_and_plan(tmp_path,monkeypatch):
    worker,client=fixture(tmp_path);record=spool(worker,command(worker));remote(monkeypatch,record.model_copy(update={'phase':'unknown'}))
    asyncio.run(worker.publish(client))
    assert (worker.spools/record.plan.result_id/'result.json').is_file()
    assert not read_json(worker.path(record.plan.result_id))['done']


def test_commit_ack_loss_reopen_only_queries_and_cleans_same_result(tmp_path,monkeypatch):
    # A lost successful commit ACK leaves the original unfinished plan and sealed spool.
    worker,client=fixture(tmp_path);record=spool(worker,command(worker));calls=remote(monkeypatch,record)
    reopened=RunnerResults(worker.kernel,worker.root,worker.directory.parent)
    asyncio.run(reopened.publish(client))
    assert len(calls)==1 and not (worker.spools/record.plan.result_id).exists()
    assert reopened.pending()==[]


def test_interrupted_cleanup_retries_same_plan_without_recapturing(tmp_path,monkeypatch,caplog):
    worker,client=fixture(tmp_path);record=spool(worker,command(worker));remote(monkeypatch,record)
    original=os.unlink;removed=[]
    def interrupted(path,*args,**kwargs):
        if str(path)==record.bundle.files[1].artifact.artifact_id and not removed:
            removed.append(True);raise OSError('controlled private failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(os,'unlink',interrupted)
    asyncio.run(worker.publish(client))
    assert worker.path(record.plan.result_id).is_file()
    assert 'runner_result_cleanup_failed' in caplog.text and 'controlled private failure' not in caplog.text
    monkeypatch.setattr(os,'unlink',original)
    reopened=RunnerResults(worker.kernel,worker.root,worker.directory.parent)
    assert reopened.pending()==[]
    assert not worker.path(record.plan.result_id).exists() and not (worker.spools/record.plan.result_id).exists()


@pytest.mark.parametrize('link',['spool','files','artifact'])
def test_retirement_refuses_links_and_preserves_foreign_target(tmp_path,monkeypatch,caplog,link):
    worker,client=fixture(tmp_path);record=spool(worker,command(worker));remote(monkeypatch,record)
    root=worker.spools/record.plan.result_id;outside=tmp_path/'outside';outside.mkdir();(outside/'keep').write_text('safe')
    if link=='spool':root.rename(worker.spools/'saved');root.symlink_to(outside,target_is_directory=True)
    elif link=='files':(root/'files').rename(root/'saved');(root/'files').symlink_to(outside,target_is_directory=True)
    else:
        path=root/'files'/record.bundle.files[0].artifact.artifact_id;path.unlink();path.symlink_to(outside/'keep')
    asyncio.run(worker.publish(client))
    assert worker.path(record.plan.result_id).is_file() and (outside/'keep').read_text()=='safe'
    assert 'runner_result_cleanup_failed' in caplog.text


def test_true_pending_plans_still_obey_capacity(tmp_path):
    worker,_=fixture(tmp_path);worker.kernel.bootstrap=worker.kernel.bootstrap.model_copy(update={
        'result_limits':worker.kernel.bootstrap.result_limits.model_copy(update={'max_results':1})})
    worker.admit(command(worker))
    worker.admit(command(worker))  # One unconfirmed handoff may overlap retained capacity.
    assert len(worker.pending())==2
    with pytest.raises(CloudError,match='result_capacity'):worker.admit(command(worker))


def test_one_handoff_plan_is_admitted_before_superseded_plan_is_reconciled(tmp_path):
    worker,_=fixture(tmp_path);worker.kernel.bootstrap=worker.kernel.bootstrap.model_copy(update={
        'result_limits':worker.kernel.bootstrap.result_limits.model_copy(update={'max_results':1})})
    previous=command(worker);worker.admit(previous)
    # Coordinator has accepted a new run and superseded the previous pending result,
    # but Runner's publisher has not yet GET-confirmed that old result's new phase.
    incoming=command(worker);worker.admit(incoming)
    assert {value['plan']['result_id'] for value in worker.pending()}=={
        previous.result_plan.result_id,incoming.result_plan.result_id}
    assert not read_json(worker.path(previous.result_plan.result_id))['done']
    with pytest.raises(CloudError,match='result_capacity'):worker.admit(command(worker))


def test_cleanup_failure_does_not_block_other_confirmed_result(tmp_path,monkeypatch,caplog):
    worker,client=fixture(tmp_path);first=spool(worker,command(worker));second=spool(worker,command(worker))
    broken=worker.spools/first.plan.result_id/'files'/first.bundle.files[0].artifact.artifact_id
    broken.unlink();outside=tmp_path/'keep';outside.write_text('safe');broken.symlink_to(outside)
    records={record.plan.result_id:record for record in (first,second)}
    async def request(http,b,method,path,**kwargs):
        assert method=='GET'
        return records[path.rsplit('/',1)[1]].model_dump(mode='json')
    monkeypatch.setattr(module,'task_request',request)
    asyncio.run(worker.publish(client))
    assert worker.path(first.plan.result_id).exists() and not worker.path(second.plan.result_id).exists()
    assert not (worker.spools/second.plan.result_id).exists() and outside.read_text()=='safe'
    assert 'runner_result_cleanup_failed' in caplog.text


def test_cleanup_rejects_replaced_artifact_after_interruption(tmp_path,monkeypatch,caplog):
    worker,client=fixture(tmp_path);record=spool(worker,command(worker));remote(monkeypatch,record)
    original=os.unlink
    def interrupted(path,*args,**kwargs):raise OSError('controlled interruption before unlink')
    monkeypatch.setattr(os,'unlink',interrupted);asyncio.run(worker.publish(client))
    monkeypatch.setattr(os,'unlink',original)
    path=worker.spools/record.plan.result_id/'files'/record.bundle.files[0].artifact.artifact_id
    path.rename(path.with_name('original'));path.write_text('replacement');path.chmod(0o600)
    # Remove the moved original from the fixed spool; the replacement must remain untouched.
    path.with_name('original').unlink()
    assert worker.pending()==[]
    assert path.read_text()=='replacement' and worker.path(record.plan.result_id).is_file()
    assert 'result_cleanup_changed' in caplog.text


def test_remote_deleting_is_unconfirmed_and_keeps_retransmission_spool(tmp_path,monkeypatch):
    worker,client=fixture(tmp_path);record=spool(worker,command(worker))
    remote(monkeypatch,record.model_copy(update={'phase':'deleting'}))
    asyncio.run(worker.publish(client))
    assert (worker.spools/record.plan.result_id/'result.json').is_file()
    assert not read_json(worker.path(record.plan.result_id))['done']


def test_old_done_plan_cannot_delete_spool_for_another_task(tmp_path,caplog):
    worker,_=fixture(tmp_path);record=spool(worker,command(worker));path=worker.path(record.plan.result_id)
    local=read_json(path);local['done']=True;local['plan']['task_id']=uuid4().hex;write_json(path,local)
    assert worker.pending()==[]
    assert (worker.spools/record.plan.result_id/'result.json').is_file() and path.is_file()
    assert 'result_identity_conflict' in caplog.text


@pytest.mark.parametrize('stage',['files','metadata','spool','plan'])
def test_cleanup_interruption_at_each_boundary_keeps_retry_anchor(tmp_path,monkeypatch,stage):
    worker,client=fixture(tmp_path);record=spool(worker,command(worker));remote(monkeypatch,record)
    original_unlink,original_rmdir=os.unlink,os.rmdir
    def unlink(path,*args,**kwargs):
        if (stage=='metadata' and str(path)=='result.json' or
                stage=='plan' and str(path)==record.plan.result_id+'.json'):
            raise OSError('controlled cleanup boundary')
        return original_unlink(path,*args,**kwargs)
    def rmdir(path,*args,**kwargs):
        if stage=='files' and str(path)=='files' or stage=='spool' and str(path)==record.plan.result_id:
            raise OSError('controlled cleanup boundary')
        return original_rmdir(path,*args,**kwargs)
    monkeypatch.setattr(os,'unlink',unlink);monkeypatch.setattr(os,'rmdir',rmdir)
    asyncio.run(worker.publish(client))
    assert worker.path(record.plan.result_id).is_file()
    monkeypatch.setattr(os,'unlink',original_unlink);monkeypatch.setattr(os,'rmdir',original_rmdir)
    reopened=RunnerResults(worker.kernel,worker.root,worker.directory.parent)
    assert reopened.pending()==[] and not worker.path(record.plan.result_id).exists()
    assert not (worker.spools/record.plan.result_id).exists()


def test_real_commit_ack_loss_reopen_retires_published_bytes(tmp_path):
    import shutil
    import httpx
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from naiyue_test_agent.service.cloud.app import create_cloud_apps
    from lifecycle_helpers import config
    from test_runner_control import prepared,client,connect
    from test_result_api import frozen
    with open_cloud_runtime(config(tmp_path)) as runtime:
        b=prepared(runtime)
        with client(runtime) as owner,connect(owner,runtime,b) as ws:
            rid,_,sealed,snapshot=frozen(runtime,b,owner,ws,tmp_path);plan=snapshot.plan
            kernel=SimpleNamespace(bootstrap=b,journal=SimpleNamespace(lookup=lambda _:dict(status='completed',terminal_seq=snapshot.boundary.seq)),
                session_quiet=lambda:True,phase='ready',last_run_id=plan.source_run_id,recovery_info=None)
            state=tmp_path/'runner';state.mkdir(mode=0o700);worker=RunnerResults(kernel,tmp_path/'work',state)
            worker.admit(RunCommand(command_id=plan.source_command_id,run_id=plan.source_run_id,message='original',model_id='A',result_plan=plan))
            shutil.copytree(sealed,worker.spools/rid)
            _,app=create_cloud_apps(runtime)
            class LostCommit(httpx.AsyncBaseTransport):
                def __init__(self):self.transport=httpx.ASGITransport(app=app);self.lost=False;self.after=[]
                async def handle_async_request(self,request):
                    if self.lost:self.after.append((request.method,request.url.path))
                    response=await self.transport.handle_async_request(request)
                    if request.url.path.endswith('/results/'+rid+'/commit') and response.status_code==200 and not self.lost:
                        self.lost=True;await response.aclose();raise httpx.ReadError('controlled successful ACK loss')
                    return response
            transport=LostCommit()
            async def scenario():
                async with httpx.AsyncClient(transport=transport,base_url='http://'+runtime.settings.task_listen) as http:
                    async def send(_):pass
                    remote_client=SimpleNamespace(http=http,bootstrap=b,send=send)
                    with pytest.raises(httpx.ReadError):await worker.publish(remote_client)
                    assert (worker.spools/rid/'result.json').is_file()
                    reopened=RunnerResults(kernel,worker.root,state);await reopened.publish(remote_client)
                    assert reopened.pending()==[]
            asyncio.run(scenario())
            assert runtime.results.get(b.task_id,rid).bundle==snapshot.bundle
            assert transport.after==[('GET','/internal/cloud/v1/tasks/'+b.task_id+'/results/'+rid)]
            assert list(worker.spools.iterdir())==[] and list(worker.directory.iterdir())==[]
