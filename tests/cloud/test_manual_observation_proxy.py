import asyncio
from types import SimpleNamespace
import httpx
import pytest
from test_manual_task_commands import prepared
from naiyue_test_agent.application.cloud_local.observation import observe
from naiyue_test_agent.cloud_files.client import CloudClient, CloudConnection
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_async_client_keeps_owner_headers_and_releases_bounded_frames(monkeypatch):
    original=httpx.AsyncClient;seen=[];closed=[]
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'event: heartbeat\nda';yield b'ta: {}\n\n'
        async def aclose(self):closed.append(True)
    async def handler(request):
        seen.append(request)
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Body())
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    cfg=CloudConnection(endpoint='http://127.0.0.1:32101',owner_authority='127.0.0.1:32102',
        owner_token='fixture-owner',instance_id='a'*8+'-aaaa-aaaa-aaaa-'+'a'*12)
    client=CloudClient(cfg)
    async def run():
        assert [part async for part in client.observe('b'*32,last_event_id='cursor',max_frame_bytes=100)]==[b'event: heartbeat\ndata: {}\n\n']
    try:asyncio.run(run())
    finally:client.close()
    assert closed and seen[0].headers['host']==cfg.owner_authority
    assert seen[0].headers['authorization']=='Bearer fixture-owner'
    assert seen[0].headers['last-event-id']=='cursor' and 'cursor' not in str(seen[0].url)


def test_observer_shutdown_cancels_only_local_read_and_releases_connection(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        entered=asyncio.Event();released=[]
        async def stream(*args,**kwargs):
            try:entered.set();await asyncio.Event().wait();yield b''
            finally:released.append(True)
        remote.observe=stream
        async def read():return [part async for part in observe(manager,domain,binding['binding_id'],sid)]
        task=asyncio.create_task(read())
        try:
            await asyncio.wait_for(entered.wait(),5)
            await asyncio.wait_for(manager.close(),5)
            assert task.done() and task.cancelled() and released==[True]
            assert not manager.observers and remote.closed
            assert not any(path.endswith(('/control','/delete')) for path,_ in remote.posts)
        finally:store.close()
    asyncio.run(run())


def test_stream_frame_budget_fails_before_forwarding_large_payload(monkeypatch):
    original=httpx.AsyncClient
    async def handler(request):return httpx.Response(200,headers={'content-type':'text/event-stream'},content=b'data: '+b'x'*100+b'\n\n')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    cfg=CloudConnection(endpoint='http://127.0.0.1:32101',owner_authority='127.0.0.1:32102',owner_token='fixture-owner',
        instance_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    client=CloudClient(cfg)
    async def run():
        with pytest.raises(CloudError,match='cloud_response_too_large'):
            _=[part async for part in client.observe('b'*32,max_frame_bytes=32)]
    try:asyncio.run(run())
    finally:client.close()


def test_real_local_stream_route_precedes_generic_view_and_pins_binding(tmp_path,monkeypatch):
    from test_manual_commands_routes import configured
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            seen=[]
            async def stream(task_id,**kwargs):
                seen.append((task_id,kwargs));yield b'event: heartbeat\ndata: {}\n\n'
            t.adapter.observe=stream
            path=t.base+'/cloud-tasks/'+t.bid+'/stream'
            denied=await t.client.get(path,params=t.query,headers=t.headers|{'Authorization':''})
            assert denied.status_code==401 and not seen
            response=await t.client.get(path,params=t.query,headers=t.headers|{'Last-Event-ID':'cursor'})
            assert response.status_code==200 and 'event: heartbeat' in response.text
            assert seen[0][0]==t.remote.task['task_id'] and seen[0][1]['last_event_id']=='cursor'
    asyncio.run(run())


def test_stream_cleanup_does_not_replace_primary_send_error():
    from naiyue_test_agent.service.cloud_observation import ClientObservationResponse
    async def run():
        primary=RuntimeError('controlled_send_failed')
        async def body():
            try:yield b'data: {}\n\n'
            finally:raise OSError('controlled_secondary_cleanup')
        async def receive():await asyncio.Event().wait()
        async def send(message):
            if message['type']=='http.response.body':raise primary
        settings=SimpleNamespace(stream_seconds=5,stream_send_timeout_seconds=1)
        with pytest.raises(RuntimeError) as captured:
            await ClientObservationResponse(body(),settings)({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
        assert captured.value is primary
    asyncio.run(run())


def test_observer_capacity_returns_safe_event_without_borrowing_or_stopping_remote(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        manager.settings=manager.settings.model_copy(update={'max_observers':1})
        occupied=object();manager.observers.add(occupied)
        try:
            frames=[part async for part in observe(manager,domain,binding['binding_id'],sid)]
            assert len(frames)==1 and b'cloud_observer_capacity' in frames[0]
            assert manager.observers=={occupied}
            assert not any(path.endswith(('/control','/delete')) for path,_ in remote.posts)
        finally:manager.observers.clear();await manager.close();store.close()
    asyncio.run(run())


def test_close_waits_for_upstream_cleanup_while_downstream_is_between_frames(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        yielded=asyncio.Event();cleaning=asyncio.Event();release=asyncio.Event();finished=[];released=[]
        async def release_connection(client):released.append(bool(finished))
        manager.connections.release=release_connection
        async def stream(*args,**kwargs):
            try:yield b'data: {}\n\n'
            finally:cleaning.set();await release.wait();finished.append(True)
        remote.observe=stream
        async def reader():
            iterator=observe(manager,domain,binding['binding_id'],sid)
            try:
                await anext(iterator);yielded.set();await asyncio.Event().wait()
            finally:await iterator.aclose()
        task=asyncio.create_task(reader())
        try:
            await asyncio.wait_for(yielded.wait(),5);closing=asyncio.create_task(manager.close())
            await asyncio.wait_for(cleaning.wait(),5)
            assert not closing.done() and not remote.closed
            release.set();await asyncio.wait_for(closing,5)
            assert finished==[True] and released==[True] and task.done() and remote.closed and not manager.observers
        finally:release.set();await manager.close();store.close()
    asyncio.run(run())
