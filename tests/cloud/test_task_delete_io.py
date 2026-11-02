import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4
from types import SimpleNamespace
import pytest
from lifecycle_helpers import Backend, config, register, template
from test_task_delete import request
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.contracts.cloud_tasks import CloudError


@pytest.mark.parametrize('timeout',[False,True])
def test_file_io_drains_or_retains_stage_on_timeout(tmp_path,monkeypatch,timeout):
    settings=config(tmp_path,delete_drain_timeout_seconds=0.1 if timeout else 30.0)
    with open_cloud_runtime(settings) as r:
        async def run():
            async with open_cloud_backend(r,backend=Backend()):
                task=register(r);parent=settings.data_dir/'tasks'/task.task_id;parent.mkdir(parents=True)
                entered=Event();release=Event();reserved=asyncio.Event();loop=asyncio.get_running_loop();old=r.deletions.reserve
                def reserve(*args):
                    result=old(*args);loop.call_soon_threadsafe(reserved.set);return result
                monkeypatch.setattr(r.deletions,'reserve',reserve)
                def writer():
                    with r.task_io.hold(task.task_id):
                        entered.set();assert release.wait(5);(parent/'late').write_text('late')
                with ThreadPoolExecutor(1) as pool:
                    future=pool.submit(writer)
                    assert await asyncio.to_thread(entered.wait,5)
                    value=request(r,task.task_id);job=asyncio.create_task(r.deletions.command(task.task_id,value))
                    await asyncio.wait_for(reserved.wait(),5)
                    assert parent.exists()
                    if timeout:
                        receipt=await asyncio.wait_for(job,5)
                        assert receipt.status=='unknown' and receipt.error_code=='task_delete_io_pending'
                        assert r.resources.capacity()['counts']['retained']==1
                    else:assert not job.done()
                    release.set();await asyncio.wrap_future(future)
                    receipt=await r.deletions.reconcile(value['command_id']) if timeout else await job
                    assert receipt.status=='succeeded' and not parent.exists()
                assert not r.task_io.active
        asyncio.run(run())


def test_bootstrap_second_phase_cannot_recreate_after_delete(tmp_path,monkeypatch):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def run():
            async with open_cloud_backend(r,backend=Backend()):
                task=register(r);parent=r.settings.data_dir/'tasks'/task.task_id;parent.mkdir(parents=True)
                issued=Event();release=Event();original=r.credentials.issue
                def pause(*args,**kwargs):
                    result=original(*args,**kwargs);issued.set();assert release.wait(5);return result
                monkeypatch.setattr(r.credentials,'issue',pause)
                with ThreadPoolExecutor(1) as pool:
                    preparing=pool.submit(r.control.prepare,task.task_id)
                    assert await asyncio.to_thread(issued.wait,5)
                    try:
                        assert (await r.deletions.command(task.task_id,request(r,task.task_id))).status=='succeeded'
                    finally:release.set()
                    with pytest.raises(CloudError,match='task_not_available'):await asyncio.wrap_future(preparing)
                assert not parent.exists()
        asyncio.run(run())


def test_existing_download_is_closed_without_consumption_and_new_open_rejected(tmp_path):
    from test_artifact_api import accept, metadata, prepare, upload, task_headers
    from test_runner_control import prepared,client,connect
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            capture,fence=accept(r,c,ws,b);data=b'confirmed';record=metadata(b,capture,data)
            path=prepare(c,r,b,record);assert upload(c,r,b,path,record,data).status_code==200
            assert c.post(path+'/commit',headers=task_headers(r,b),json={}).status_code==200
            ws.send_json(dict(kind='artifact_finished',command_id=capture['command_id'],artifact_id=record['artifact_id'],
                status='available',quiescent=True,phase='ready',**fence))
            ws.send_json(dict(kind='heartbeat',**fence));assert ws.receive_json()['kind']=='welcome'
        async def run():
            async with open_cloud_backend(r,backend=Backend()):
                current=r.registry.get_task(b.task_id);pin=template()
                r.replace_task(current.model_copy(update={'revision':current.revision+1,'template_snapshot':pin,
                    'template_digest':pin.fingerprint}),expected_revision=current.revision,expected_generation=current.generation)
                lease=await r.artifacts.open_download(b.task_id,record['artifact_id'])
                assert not lease.stream.closed
                result=await r.deletions.command(b.task_id,request(r,b.task_id,acknowledge_unknown=True))
                assert result.status=='succeeded' and lease.stream.closed and lease.finished.is_set()
                assert not r.artifacts.downloads
                with pytest.raises(CloudError,match='task_not_available'):
                    await r.artifacts.open_download(b.task_id,record['artifact_id'])
        asyncio.run(run())


def test_late_detach_and_unconfirmed_cannot_rewrite_tombstone(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def run():
            async with open_cloud_backend(r,backend=Backend()):
                task=register(r)
                assert (await r.deletions.command(task.task_id,request(r,task.task_id))).status=='succeeded'
                before=r.registry.get_task(task.task_id);cid=str(uuid4())
                for service in (r.control.runs,r.control.actions,r.artifacts):service.unconfirmed(task.task_id,cid)
                connection=SimpleNamespace(task_id=task.task_id,connection_id='old')
                r.control.connections[task.task_id]=connection
                await r.control.detach(connection)
                assert r.registry.get_task(task.task_id)==before
        asyncio.run(run())
