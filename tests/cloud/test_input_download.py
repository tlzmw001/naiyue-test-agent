"""Only an owner can stream a pinned, committed input archive by content identity."""
import asyncio
import hashlib
import threading

import httpx
import pytest

from cloud_helpers import settings,owner_headers
from test_input_formats import package
from test_input_recovery import uploaded
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.artifact_store import file_identity
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.service.cloud.app import create_cloud_apps


def test_owner_archive_download_is_immutable_range_bound_and_task_inaccessible(tmp_path):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        transfer=uploaded(runtime,captured)
        raw=(captured.directory/'archive').read_bytes()
        async def scenario():
            owner,task=create_cloud_apps(runtime)
            path='/api/cloud/v1/inputs/'+captured.input_id+'/archive'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=runtime.settings.origin) as client:
                assert (await client.get(path,headers=owner_headers(runtime))).status_code==404
                package=runtime.inputs.commit(transfer.transfer_id)
                before=file_identity((runtime.inputs.root/'inputs'/captured.input_id/'archive').stat())
                headers=owner_headers(runtime);etag='"'+captured.archive_sha256+'"'
                response=await client.get(path,headers=headers)
                assert response.status_code==200,response.text
                assert response.content==raw and hashlib.sha256(response.content).hexdigest()==package.archive_sha256
                assert response.headers['etag']==etag and response.headers['content-length']==str(package.size)
                assert response.headers['accept-ranges']=='bytes' and response.headers['x-content-type-options']=='nosniff'
                assert captured.input_id in response.headers['content-disposition']
                part=await client.get(path,headers=headers|{'Range':'bytes=1-5','If-Range':etag})
                assert part.status_code==206 and part.content==raw[1:6]
                assert part.headers['content-range']==f'bytes 1-5/{len(raw)}'
                stale=await client.get(path,headers=headers|{'Range':'bytes=1-5','If-Range':'"stale"'})
                assert stale.status_code==200 and stale.content==raw
                assert (await client.get(path,headers=headers|{'Range':'bytes=0-1,3-4'})).status_code==416
                duplicate=await client.get(path,headers=[*headers.items(),('Range','bytes=0-1'),('Range','bytes=3-4')])
                assert duplicate.status_code==400
                assert (await client.get(path)).status_code==401
                denied=await client.get(path,headers=headers|{'Authorization':'Bearer task-only-credential'})
                assert denied.status_code==401
                assert (await client.get('/api/cloud/v1/inputs/'+'e'*64+'/archive',headers=headers)).status_code==404
                assert file_identity((runtime.inputs.root/'inputs'/captured.input_id/'archive').stat())==before
                assert runtime.inputs.get_input(captured.input_id)==package
                assert not runtime.inputs.downloads and 'input:'+captured.input_id not in runtime.inputs.busy
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+runtime.settings.task_listen) as client:
                assert (await client.get(path,headers={'Authorization':'Bearer task-only-credential'})).status_code==404
        asyncio.run(scenario())


def test_input_archive_lease_pins_identity_and_close_drains_cancelled_read(tmp_path):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        transfer=uploaded(runtime,captured);runtime.inputs.commit(transfer.transfer_id)
        async def scenario():
            lease=await runtime.inputs.open_download(captured.input_id)
            assert lease in runtime.inputs.downloads
            with pytest.raises(CloudError,match='transfer_busy'):
                with runtime.inputs.exclusive('input:'+captured.input_id):pass
            original=lease.stream;entered=threading.Event();release=threading.Event();closed=[]
            class Paused:
                def read(self,size):
                    entered.set();assert release.wait(5);assert not closed
                    return original.read(size)
                def fileno(self):return original.fileno()
                def close(self):closed.append(True);original.close()
            lease.stream=Paused();reading=asyncio.create_task(anext(lease.chunks()))
            try:
                assert await asyncio.to_thread(entered.wait,5)
                reading.cancel();closing=asyncio.create_task(runtime.inputs.aclose())
                await asyncio.sleep(0)
                assert not closed and not closing.done()
            finally:release.set()
            outcome=await asyncio.gather(reading,return_exceptions=True)
            assert isinstance(outcome[0],asyncio.CancelledError)
            await closing
            assert closed==[True] and lease.finished.is_set() and not runtime.inputs.downloads
            assert 'input:'+captured.input_id not in runtime.inputs.busy
        asyncio.run(scenario())


def test_task_scoped_input_lease_is_closed_by_deletion(tmp_path):
    from lifecycle_helpers import config,register,Backend
    from test_task_delete import request
    from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
    from naiyue_test_agent.contracts.cloud_tasks import TaskAttachment
    from uuid import uuid4
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def scenario():
            async with open_cloud_backend(runtime,backend=Backend()):
                task=register(runtime);aid=str(uuid4())
                attachment=TaskAttachment(attachment_id=aid,input_id=task.input_id,display_name='files',size=1)
                updated=task.model_copy(update={'attachments':{aid:attachment},'revision':task.revision+1})
                runtime.replace_task(updated,expected_revision=task.revision,expected_generation=task.generation)
                lease=await runtime.inputs.open_download(task.input_id,task_id=task.task_id)
                assert runtime.task_io.active=={task.task_id:1}
                result=await runtime.deletions.command(task.task_id,request(runtime,task.task_id))
                assert result.status=='succeeded'
                assert lease.finished.is_set() and lease.stream.closed
                assert not runtime.task_io.active and not runtime.registry.get_task(task.task_id).attachments
                with pytest.raises(CloudError,match='task_not_available'):
                    await runtime.inputs.open_download(task.input_id,task_id=task.task_id)
                owner=await runtime.inputs.open_download(task.input_id)
                assert owner.task_id is None
                await owner.aclose()
        asyncio.run(scenario())


def test_same_immutable_input_supports_parallel_read_leases_and_keeps_writer_exclusion(tmp_path):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        value=uploaded(runtime,captured);runtime.inputs.commit(value.transfer_id)
        async def scenario():
            first=await runtime.inputs.open_download(captured.input_id)
            second=None
            try:
                second=await runtime.inputs.open_download(captured.input_id)
                assert first.stream.fileno()!=second.stream.fileno()
                await first.aclose()
                with pytest.raises(CloudError,match='transfer_busy'):
                    with runtime.inputs.exclusive('input:'+captured.input_id):pass
                assert b''.join([part async for part in second.chunks()])
            finally:
                await first.aclose()
                if second is not None:await second.aclose()
            assert 'input:'+captured.input_id not in runtime.inputs.busy
        asyncio.run(scenario())


def test_repeated_reader_close_cannot_release_a_later_writer(tmp_path):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        transfer=uploaded(runtime,captured);runtime.inputs.commit(transfer.transfer_id)
        async def scenario():
            lease=await runtime.inputs.open_download(captured.input_id);await lease.aclose()
            key='input:'+captured.input_id
            with runtime.inputs.exclusive(key):
                await asyncio.gather(lease.aclose(),lease.aclose())
                assert key in runtime.inputs.busy
                with pytest.raises(CloudError,match='transfer_busy'):await runtime.inputs.open_download(captured.input_id)
            assert key not in runtime.inputs.busy
        asyncio.run(scenario())
