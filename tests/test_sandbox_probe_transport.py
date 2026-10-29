"""Persistent probe framing, concurrent routing and bounded failure handling."""
import asyncio
import json
import sys
import pytest

IDENTITY={'binding_id':'B','kind':'sandbox','project_id':'P','root':'/workspace','platform':'linux','project_revision':None}


def program(body):
    return ('import sys,json\n'
        'ready='+repr(dict(probe='ready',version=1,protocol=2,environment=IDENTITY,session_id='S',owned=False))+'\n'
        'print(json.dumps(ready),flush=True)\n'+body)


def test_out_of_order_responses_are_routed_to_original_requests():
    from scripts.sandbox_probe.transport import ProbeChannel
    source=program("a=json.loads(sys.stdin.readline());b=json.loads(sys.stdin.readline())\n"
        "for r in [b,a]:\n r.update(ok=True,result=r['params']);print(json.dumps(r),flush=True)\n"
        "sys.stdin.read()\n")
    async def run():
        async with await ProbeChannel.open([sys.executable,'-u','-c',source],expected=IDENTITY,session_id='S') as channel:
            first,second=await asyncio.gather(channel.request('hello',{'number':1}),channel.request('hello',{'number':2}))
            assert first=={'number':1} and second=={'number':2}
            assert channel.sent_count==2 and channel.received_count==2
    asyncio.run(run())


@pytest.mark.parametrize('response',[{'request_id':'foreign','ok':True,'result':{}}, {'ok':True,'result':{},'session_id':'other'}])
def test_invalid_response_identity_fails_channel(response):
    from scripts.sandbox_probe.transport import ProbeChannel,ProbeError
    source=program("r=json.loads(sys.stdin.readline());r.update("+repr(response)+");print(json.dumps(r),flush=True)\nsys.stdin.read()\n")
    async def run():
        async with await ProbeChannel.open([sys.executable,'-u','-c',source],expected=IDENTITY,session_id='S') as channel:
            with pytest.raises(ProbeError,match='probe_protocol'):await channel.request('hello',{})
            assert channel.failed
    asyncio.run(run())


def test_disconnect_fails_inflight_without_resubmitting():
    from scripts.sandbox_probe.transport import ProbeChannel,ProbeError
    source=program('sys.stdin.readline()\n')
    async def run():
        async with await ProbeChannel.open([sys.executable,'-u','-c',source],expected=IDENTITY,session_id='S') as channel:
            with pytest.raises(ProbeError,match='probe_eof'):await channel.request('operation_submit',{})
            assert channel.sent_count==1
    asyncio.run(run())


def test_message_limit_rejected_before_sending():
    from scripts.sandbox_probe.transport import ProbeChannel,ProbeError
    source=program('sys.stdin.read()\n')
    async def run():
        async with await ProbeChannel.open([sys.executable,'-u','-c',source],expected=IDENTITY,session_id='S',limit=1024) as channel:
            with pytest.raises(ProbeError,match='probe_frame_limit'):await channel.request('hello',{'text':'x'*2000})
            assert channel.sent_count==0
    asyncio.run(run())


def test_request_deadline_closes_inflight_state():
    from scripts.sandbox_probe.transport import ProbeChannel,ProbeError
    source=program('sys.stdin.readline();sys.stdin.read()\n')
    async def run():
        async with await ProbeChannel.open([sys.executable,'-u','-c',source],expected=IDENTITY,session_id='S',timeout=.25) as channel:
            with pytest.raises(ProbeError,match='probe_timeout'):await channel.request('hello',{})
            assert channel.failed and channel.sent_count==1
        assert channel.process.returncode is not None
    asyncio.run(run())


def test_stderr_is_drained_without_exposing_its_content():
    from scripts.sandbox_probe.transport import ProbeChannel
    source=program("sys.stderr.write('PRIVATE_TOKEN'*1000);sys.stderr.flush()\n"
        "r=json.loads(sys.stdin.readline());r.update(ok=True,result={});print(json.dumps(r),flush=True)\nsys.stdin.read()\n")
    async def run():
        async with await ProbeChannel.open([sys.executable,'-u','-c',source],expected=IDENTITY,session_id='S') as channel:
            assert await channel.request('hello',{})=={}
        assert channel.stderr_bytes>0 and not hasattr(channel,'stderr_text')
    asyncio.run(run())
