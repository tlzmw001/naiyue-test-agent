"""Persistent result plans reconcile lost ACKs without rescanning or replaying a run."""
import asyncio,shutil
from types import SimpleNamespace
from pathlib import Path
import httpx,pytest
from naiyue_test_agent.contracts.cloud_runner import RunCommand
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_runner.results import RunnerResults
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from test_result_api import frozen
from lifecycle_helpers import config
from test_runner_control import prepared,client,connect


@pytest.mark.parametrize('lost',['prepare','bytes','commit'])
def test_frozen_result_ack_loss_resumes_same_bundle_after_publisher_reopen(tmp_path,lost):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            rid,raw,spool,snapshot=frozen(r,b,c,ws,tmp_path);plan=snapshot.plan
            root=tmp_path/'work';state=tmp_path/'runner';state.mkdir(mode=0o700)
            kernel=SimpleNamespace(bootstrap=b,journal=SimpleNamespace(lookup=lambda _:dict(status='completed',terminal_seq=snapshot.boundary.seq)),
                session_quiet=lambda:True,phase='ready',last_run_id=plan.source_run_id,recovery_info=None)
            results=RunnerResults(kernel,root,state);kernel.results=results
            results.admit(RunCommand(command_id=plan.source_command_id,run_id=plan.source_run_id,message='original',model_id='A',result_plan=plan))
            shutil.copytree(spool,results.spools/rid);(root/'new.py').write_text('must never be rescanned')
            owner,task=create_cloud_apps(r)
            class LostAck(httpx.AsyncBaseTransport):
                def __init__(self):self.real=httpx.ASGITransport(app=task);self.lost=False
                async def handle_async_request(self,request):
                    reply=await self.real.handle_async_request(request)
                    if request.url.path.endswith('/'+lost) and reply.status_code==200 and not self.lost:
                        self.lost=True;await reply.aclose();raise httpx.ReadError('controlled ACK loss')
                    return reply
            transport=LostAck()
            async def scenario():
                async with httpx.AsyncClient(transport=transport,base_url='http://'+r.settings.task_listen) as http:
                    async def send(_):pass
                    remote=SimpleNamespace(http=http,bootstrap=b,send=send)
                    with pytest.raises(httpx.ReadError):await results.publish(remote)
                    reopened=RunnerResults(kernel,root,state);kernel.results=reopened
                    await reopened.publish(remote)
                    assert transport.lost and reopened.pending()==[]
            asyncio.run(scenario())
            stored=r.results.get(b.task_id,rid)
            assert stored.phase=='available' and stored.bundle==snapshot.bundle
            assert (root/'new.py').read_text()=='must never be rescanned'


def test_started_attempt_without_complete_spool_requires_explicit_retry(tmp_path):
    from naiyue_test_agent.cloud_runner.state import write_json
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            rid,raw,spool,snapshot=frozen(r,b,c,ws,tmp_path);plan=snapshot.plan
            kernel=SimpleNamespace(bootstrap=b,journal=SimpleNamespace(lookup=lambda _:dict(status='recovered',terminal_seq=snapshot.boundary.seq)),
                session_quiet=lambda:True,phase='ready',last_run_id=plan.source_run_id,recovery_info=None)
            # Preserve a failed terminal's seq when mutable execution recovery changes status.
            from naiyue_test_agent.contracts.cloud_results import ResultBoundary
            receipt=r.registry.get_command(rid);record=r.results.get(b.task_id,rid)
            boundary=record.boundary.model_copy(update={'outcome':'failed'})
            row=r.registry.get_task(b.task_id)
            r.results.repository.finish(row,row.model_copy(update={'revision':row.revision+1}),receipt.model_copy(update={'result':record.model_copy(update={'boundary':boundary}).model_dump(mode='json')}))
            state=tmp_path/'state';state.mkdir(mode=0o700);worker=RunnerResults(kernel,tmp_path/'work',state);kernel.results=worker
            worker.admit(RunCommand(command_id=plan.source_command_id,run_id=plan.source_run_id,message='original',model_id='A',result_plan=plan))
            local=worker.pending()[0];local['started_attempt']=0;write_json(worker.path(rid),local)
            _,task=create_cloud_apps(r)
            async def scenario():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+r.settings.task_listen) as http:
                    async def send(_):pass
                    await worker.publish(SimpleNamespace(http=http,bootstrap=b,send=send))
            asyncio.run(scenario())
            failed=r.results.get(b.task_id,rid)
            assert failed.phase=='failed' and failed.error_code=='result_capture_unconfirmed'
            assert failed.boundary==boundary and not (worker.spools/rid).exists()
