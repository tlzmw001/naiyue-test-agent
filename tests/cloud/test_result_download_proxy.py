"""Browser export borrows a fixed binding and releases it on cancellation."""
import asyncio,hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
import httpx,pytest
from test_manual_task_commands import prepared
from naiyue_test_agent.cloud_files.client import CloudConnection
from naiyue_test_agent.application.cloud_local.result_download import stream


def test_private_result_proxy_stream_and_cancel_release_all_scopes(tmp_path,monkeypatch):
    original=httpx.AsyncClient;requests=[];closed=[];data=b'fixture report';sha=hashlib.sha256(data).hexdigest();rid=str(uuid4());fid=str(uuid4())
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):yield data
        async def aclose(self):closed.append(True)
    async def forward(request):
        requests.append(request)
        return httpx.Response(200,headers={'etag':'"'+sha+'"','content-length':str(len(data))},stream=Body())
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(forward),**kw))
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        cfg=CloudConnection(endpoint='http://127.0.0.1:32101',owner_authority='127.0.0.1:32102',owner_token='fixture-owner',instance_id=binding['instance_id'],io_bytes=3)
        plan=dict(result_id=rid,source_command_id=str(uuid4()),source_run_id='a'*32,task_id=binding['task_id'],project_id=binding['cloud_project_id'],input_id=binding['input_id'],source_generation=1,output_directory='.nyt-output')
        boundary=dict(kind='run',epoch=str(uuid4()),seq=1,outcome='completed')
        def file(role,ident,name):return dict(role=role,artifact=dict(command_id=rid,artifact_id=ident,relative_name=name,task_id=binding['task_id'],project_id=binding['cloud_project_id'],input_id=binding['input_id'],generation=1,captured_at=1.0,size=len(data),sha256=sha))
        record=dict(plan=plan,phase='available',boundary=boundary,bundle=dict(plan=plan,boundary=boundary,generation=1,captured_at=1.0,files=[file('manifest',str(uuid4()),'manifest.json'),file('patch',fid,'changes.patch')]))
        client=SimpleNamespace(settings=cfg,http=SimpleNamespace(headers={'Host':cfg.owner_authority,'Authorization':'Bearer fixture-owner'}),request=lambda *a,**kw:record)
        borrowed=[]
        @asynccontextmanager
        async def connection(_):
            borrowed.append(True)
            try:yield client
            finally:borrowed.pop()
        manager.connection=connection
        try:
            iterator=stream(manager,domain,binding['binding_id'],sid,rid,fid,{})
            status,headers=await anext(iterator);assert status==200 and borrowed and manager.operations
            assert b''.join([part async for part in iterator])==data
            assert not borrowed and not manager.operations
            cancelled=stream(manager,domain,binding['binding_id'],sid,rid,fid,{})
            await anext(cancelled);await cancelled.aclose()
            assert not borrowed and not manager.operations and len(closed)==2
            paused=stream(manager,domain,binding['binding_id'],sid,rid,fid,{})
            await anext(paused)
            async with asyncio.timeout(5):await manager.close()
            assert not manager.downloads and not manager.operations and not borrowed
            await paused.aclose()
            assert all(q.headers['authorization']=='Bearer fixture-owner' and q.headers['host']==cfg.owner_authority for q in requests)
            assert all('fixture-owner' not in str(q.url) for q in requests)
        finally:await manager.close();store.close()
    asyncio.run(scenario())


def test_download_cancel_before_producer_starts_returns_capacity():
    from naiyue_test_agent.application.cloud_local.download_lease import DownloadLease
    async def scenario():
        manager=SimpleNamespace(closing=False,downloads=set(),observers=set(),settings=SimpleNamespace(max_observers=1))
        async def source():yield b'never started'
        lease=DownloadLease(manager,source());await lease.aclose()
        assert lease.job.cancelled() and not manager.downloads
    asyncio.run(scenario())
