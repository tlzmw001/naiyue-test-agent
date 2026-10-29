"""An unconnected Unix socket must never carry an envd request."""
import asyncio
import json
import os
import sys
import pytest

from test_sandbox_probe_transport import IDENTITY


def test_unconnected_socket_waits_for_admission_before_one_write():
    from scripts.sandbox_probe.transport import ProbeChannel
    source="""import asyncio,os
from scripts.sandbox_probe.worker import serve
async def main():
    loop=asyncio.get_running_loop();connect=loop.sock_connect;attempts=0
    async def congested(sock,path):
        nonlocal attempts
        attempts+=1
        # Linux asyncio can report success after EAGAIN while still unconnected.
        if attempts==1:return
        await connect(sock,path)
    loop.sock_connect=congested
    await serve(IDENTITY,'S',os.environ['NYT_PROBE_SOCKET'],owned=True)
    assert attempts==2
asyncio.run(main())
""".replace('IDENTITY',repr(IDENTITY))

    async def run():
        calls=[]
        async def receive(reader,writer):
            request=json.loads(await reader.readline());calls.append(request)
            writer.write(json.dumps({**request,'ok':True,'result':{'accepted':True}}).encode()+b'\n')
            await writer.drain();writer.close();await writer.wait_closed()
        # macOS Unix socket paths have a small limit; tmp_path itself can be long.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='nyt-uds-',dir='/tmp') as directory:
            path=directory+'/envd.sock'
            server=await asyncio.start_unix_server(receive,path=path)
            try:
                env={**os.environ,'NYT_PROBE_SOCKET':path}
                async with server,await ProbeChannel.open([sys.executable,'-u','-c',source],
                    expected=IDENTITY,session_id='S',env=env,timeout=5) as channel:
                    result=await channel.request('operation_submit',{'operation_id':'once'})
                    assert result=={'accepted':True}
                assert channel.process.returncode==0
                assert len(calls)==1 and calls[0]['params']=={'operation_id':'once'}
            finally:server.close();await server.wait_closed()
    asyncio.run(run())


def test_cancel_closes_socket_while_connect_is_pending(monkeypatch):
    from scripts.sandbox_probe.worker import connected_stream
    async def run():
        entered=asyncio.Event();pending=asyncio.Event();sockets=[]
        async def blocked(sock,path):
            sockets.append(sock);entered.set();await pending.wait()
        monkeypatch.setattr(asyncio.get_running_loop(),'sock_connect',blocked)
        task=asyncio.create_task(connected_stream('unused',262144))
        await asyncio.wait_for(entered.wait(),5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert len(sockets)==1 and sockets[0].fileno()==-1
    asyncio.run(run())


def test_admission_uses_outer_deadline_and_closes_all_attempts(monkeypatch):
    from scripts.sandbox_probe.worker import connected_stream
    async def run():
        second_attempt=asyncio.Event();sockets=[]
        async def unconnected(sock,path):
            sockets.append(sock)
            if len(sockets)==2:second_attempt.set()
        loop=asyncio.get_running_loop()
        monkeypatch.setattr(loop,'sock_connect',unconnected)
        async def expire():
            await second_attempt.wait();deadline.reschedule(loop.time())
        async with asyncio.timeout(5):
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(None) as deadline:
                    expiry=asyncio.create_task(expire())
                    try:await connected_stream('unused',262144)
                    finally:await expiry
        assert len(sockets)>=2 and all(sock.fileno()==-1 for sock in sockets)
    asyncio.run(run())


def test_connection_refused_is_not_retried(monkeypatch):
    from scripts.sandbox_probe.worker import connected_stream
    async def run():
        sockets=[]
        async def refused(sock,path):
            sockets.append(sock);raise ConnectionRefusedError('fixed test error')
        monkeypatch.setattr(asyncio.get_running_loop(),'sock_connect',refused)
        with pytest.raises(ConnectionRefusedError):await connected_stream('unused',262144)
        assert len(sockets)==1 and sockets[0].fileno()==-1
    asyncio.run(run())
