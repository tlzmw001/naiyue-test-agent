"""Real child transport: multiplexing, loss, cancellation and cleanup."""
import asyncio
import sys

import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError

ENVIRONMENT={'binding_id':'a','kind':'sandbox','project_id':'p','root':'/workspace','platform':'linux','project_revision':None}


def child(body):
    return '''import asyncio,json,sys
from naiyue_test_agent.envd.framing import FrameStream
async def main():
 loop=asyncio.get_running_loop();r=asyncio.StreamReader(limit=65536)
 await loop.connect_read_pipe(lambda:asyncio.StreamReaderProtocol(r),sys.stdin.buffer)
 t,p=await loop.connect_write_pipe(lambda:asyncio.streams.FlowControlMixin(loop=loop),sys.stdout.buffer)
 w=asyncio.StreamWriter(t,p,None,loop)
 ready=READY
 w.write(json.dumps(ready).encode()+b'\\n');await w.drain()
 frames=FrameStream(r,w,generation='generation',frame_bytes=65536,message_bytes=1048576,max_pending=10)
 BODY
 await frames.close();w.close()
asyncio.run(main())
'''.replace('READY',repr(dict(attach=1,protocol=2,generation='generation',environment=ENVIRONMENT,session_id='s'))).replace('BODY',body)


async def open_channel(body,**kwargs):
    from naiyue_test_agent.execution.sandbox_channel import SandboxChannel
    return await SandboxChannel.open([sys.executable,'-u','-c',child(body)],environment=ENVIRONMENT,
        session_id='s',message_bytes=1048576,**kwargs)


def test_channel_routes_out_of_order_responses_from_one_child():
    async def run():
        channel=await open_channel("a=await frames.receive();b=await frames.receive()\n for ident,r in [b,a]:\n  r.update(ok=True,result=r['params']);await frames.send(ident,r)\n await rread(frames)".replace('await rread(frames)','await frames.receive()'))
        try:
            assert await asyncio.gather(channel.request('read',{'x':1}),channel.request('read',{'x':2}))==[{'x':1},{'x':2}]
        finally:await channel.close()
        assert channel.process.returncode==0
    asyncio.run(run())


def test_one_cancelled_request_does_not_break_other_request():
    async def run():
        body="a=await frames.receive();b=await frames.receive()\n for ident,r in [b,a]:\n  r.update(ok=True,result=r['params']);await frames.send(ident,r)\n await frames.receive()"
        channel=await open_channel(body)
        first=asyncio.create_task(channel.request('read',{'x':1}))
        # Cancellation after transmission retains responsibility for the late reply.
        while not channel.sent_count:await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        assert await channel.request('read',{'x':2})=={'x':2}
        await channel.close()
        assert not channel.pending
    asyncio.run(run())


def test_lost_mutation_reply_is_unknown_and_not_replayed():
    async def run():
        channel=await open_channel('await frames.receive()')
        try:
            with pytest.raises(SandboxError,match='env_operation_unknown'):
                await channel.request('operation_submit',{},mutates=True)
            assert channel.sent_count==1 and channel.failed
        finally:await channel.close()
    asyncio.run(run())


def test_foreign_reply_fails_only_its_channel():
    async def run():
        channel=await open_channel("ident,r=await frames.receive();r.update(ok=True,result={},session_id='foreign')\n await frames.send(ident,r)\n await frames.receive()")
        try:
            with pytest.raises(SandboxError,match='env_protocol_mismatch'):await channel.request('read',{})
            assert channel._close_task is not None
        finally:await channel.close()
    asyncio.run(run())


def test_deadline_reaps_child_and_stderr_is_never_retained():
    async def run():
        channel=await open_channel("sys.stderr.write('secret'*500);sys.stderr.flush()\n await frames.receive();await frames.receive()",timeout=.3)
        try:
            with pytest.raises(SandboxError,match='env_request_timeout'):await channel.request('read',{})
            assert channel.failed is None and len(channel.retired)==1
        finally:await channel.close()
        assert channel.process.returncode is not None
        assert channel.stderr_bytes==3000 and not hasattr(channel,'stderr_text')
    asyncio.run(run())


def test_normal_queue_saturation_preserves_control_admission():
    async def run():
        body="while item:=await frames.receive():\n  ident,r=item\n  if r['operation']=='operation_stop':\n   r.update(ok=True,result={'stopped':True});await frames.send(ident,r)"
        channel=await open_channel(body,io_slots=1,control_slots=1,max_waiting=2)
        tasks=[asyncio.create_task(channel.request('read',{})) for _ in range(2)]
        while len(channel._calls)<2:await asyncio.sleep(0)
        try:assert await channel.request('operation_stop',{})=={'stopped':True}
        finally:
            await channel.close();await asyncio.gather(*tasks,return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('accepted',[True,False])
def test_management_preamble_is_checked_before_attached_requests(accepted):
    from naiyue_test_agent.execution.sandbox_channel import SandboxChannel
    async def scenario():
        body=child('await frames.receive()')
        body="import json,sys\nprint(json.dumps({'checked': True}),flush=True)\nassert sys.stdin.buffer.readline()==b'NYT-ATTACH-OK\\n'\n"+body
        checked=[]
        def validate(value):
            checked.append(value)
            if not accepted:raise SandboxError('env_policy_mismatch')
        if accepted:
            channel=await SandboxChannel.open([sys.executable,'-u','-c',body],environment=ENVIRONMENT,session_id='s',preamble_check=validate)
            await channel.close();assert channel.sent_count==0
        else:
            with pytest.raises(SandboxError,match='env_policy_mismatch'):
                await SandboxChannel.open([sys.executable,'-u','-c',body],environment=ENVIRONMENT,session_id='s',preamble_check=validate)
        assert checked==[{'checked':True}]
    asyncio.run(scenario())


def test_old_image_without_attach_capability_has_explicit_error():
    from naiyue_test_agent.execution.sandbox_channel import SandboxChannel
    async def scenario():
        with pytest.raises(SandboxError,match='env_attach_unavailable'):
            await SandboxChannel.open([sys.executable,'-c','raise SystemExit(2)'],environment=ENVIRONMENT,session_id='s')
    asyncio.run(scenario())
