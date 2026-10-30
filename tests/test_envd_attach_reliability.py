"""Real attach subprocess: cancellation, Unicode and response fault isolation."""
import asyncio
from contextlib import asynccontextmanager
import json
import sys
import tempfile

from naiyue_test_agent.envd.protocol import encode
from naiyue_test_agent.execution.sandbox_channel import SandboxChannel
from test_sandbox_channel import ENVIRONMENT


@asynccontextmanager
async def attached(handler,*,source='',settings=None):
    with tempfile.TemporaryDirectory(prefix='nyt-attach-test-') as directory:
        path=directory+'/s'
        ready=dict(attach=1,protocol=2,generation='generation',environment=ENVIRONMENT,session_id='s')
        async def serve(reader,writer):
            try:
                request=json.loads(await reader.readline())
                result=ready if request['operation']=='transport_hello' else await handler(request)
                writer.write(encode(dict(request,generation='generation',ok=True,result=result),65536))
                await writer.drain()
                if request['operation']=='request_register':await reader.read()
            finally:
                writer.close();await writer.wait_closed()
        server=await asyncio.start_unix_server(serve,path)
        code='\n'.join(['import asyncio',
            'from naiyue_test_agent.envd import attach as m',
            'from naiyue_test_agent.contracts.environment import EnvironmentRef',
            'from naiyue_test_agent.sandbox_settings import SandboxSettings',
            f'm.SOCKET_PATH={path!r}',source,
            f"asyncio.run(m.attach(EnvironmentRef(**{ENVIRONMENT!r}),'s',SandboxSettings(**{settings or {}})))"])
        async with server:
            channel=await SandboxChannel.open([sys.executable,'-u','-c',code],environment=ENVIRONMENT,
                session_id='s',message_bytes=65536,timeout=3)
            try:yield channel
            finally:await channel.close()


def test_surrogate_resource_path_roundtrips_through_real_attach():
    async def scenario():
        path='/workspace/.agents/skills/\udcd6\udcd0'
        async def reply(request):return request['params']
        async with attached(reply) as channel:
            assert await channel.request('resource_index',{'path':path})=={'path':path}
            assert await channel.request('read',{'path':path})=={'path':path}
            assert channel.failed is None
    asyncio.run(scenario())


def test_attach_pre_send_response_failure_does_not_kill_other_requests():
    import pytest
    from naiyue_test_agent.contracts.sandbox import SandboxError
    async def scenario():
        async def reply(request):return request['params']
        inject="""send=m.FrameStream.send
async def broken(self,ident,response,**kwargs):
 if response.get('result',{}).get('bad'):
  raise UnicodeEncodeError('utf-8','x',0,1,'test')
 return await send(self,ident,response,**kwargs)
m.FrameStream.send=broken"""
        async with attached(reply,source=inject) as channel:
            with pytest.raises(SandboxError,match='env_protocol_mismatch'):
                await channel.request('read',{'bad':True})
            assert await channel.request('read',{'good':True})=={'good':True}
            assert channel.failed is None
    asyncio.run(scenario())


def test_cancel_reaches_registered_search_and_late_reply_is_drained():
    async def scenario():
        entered=asyncio.Event();cancelled=asyncio.Event();released=asyncio.Event();seen=[]
        async def reply(request):
            op=request['operation'];seen.append(op)
            if op=='request_register':return {'registered':True}
            if op=='find':
                assert request['params']['registered'] is True
                entered.set();await asyncio.wait_for(cancelled.wait(),5)
                return 'stopped'
            if op=='request_cancel':cancelled.set();return {'cancelled':True}
            if op=='request_release':released.set();return {'cancelled':True}
            return request['params']
        async with attached(reply,settings={'rpc_timeout_seconds':1}) as channel:
            search=asyncio.create_task(channel.request('find',{'timeout':None}))
            await asyncio.wait_for(entered.wait(),5)
            search.cancel()
            try:await search
            except asyncio.CancelledError:pass
            await asyncio.wait_for(cancelled.wait(),5)
            await asyncio.wait_for(released.wait(),5)
            async with asyncio.timeout(5):
                while channel.retired:await asyncio.sleep(0)
            assert seen[:2]==['request_register','find']
            assert channel.failed is None
            assert await channel.request('read',{'after':True})=={'after':True}
    asyncio.run(scenario())
