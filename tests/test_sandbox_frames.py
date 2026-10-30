"""Finite framed messages interleave without losing their request ownership."""
import asyncio
import json

import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError


class Sink:
    def __init__(self):self.frames=[]
    def write(self,data):self.frames.append(data)
    async def drain(self):await asyncio.sleep(0)


def test_fragmented_messages_roundtrip_and_interleave_with_control():
    from naiyue_test_agent.envd.framing import FrameStream
    async def run():
        sink=Sink();source=asyncio.StreamReader(limit=1024)
        stream=FrameStream(source,sink,generation='g',frame_bytes=1024,message_bytes=16384,max_pending=2)
        await asyncio.gather(stream.send('a',{'text':'汉字\\"'*1200}),stream.send('b',{'control':True},priority=0))
        assert all(len(frame)<=1024 for frame in sink.frames)
        assert len(sink.frames)>2
        assert next(i for i,x in enumerate(sink.frames) if json.loads(x)['request_id']=='b')<len(sink.frames)-1
        for frame in sink.frames:source.feed_data(frame)
        source.feed_eof()
        responses=[await stream.receive(),await stream.receive()]
        assert dict(responses)=={'a':{'text':'汉字\\"'*1200},'b':{'control':True}}
        assert await stream.receive() is None
        await stream.close()
    asyncio.run(run())


@pytest.mark.parametrize('change',[{'offset':1},{'generation':'old'},{'request_id':''},{'end':1}])
def test_invalid_frame_is_rejected(change):
    from naiyue_test_agent.envd.framing import FrameStream
    async def run():
        source=asyncio.StreamReader();sink=Sink()
        source.feed_data(json.dumps(dict(generation='g',request_id='a',offset=0,end=True,data='{}',**{})|change).encode()+b'\n')
        stream=FrameStream(source,sink,generation='g',frame_bytes=1024,message_bytes=4096,max_pending=2)
        with pytest.raises(SandboxError,match='env_protocol_mismatch'):await stream.receive()
        await stream.close()
    asyncio.run(run())


def test_incomplete_messages_are_bounded_and_eof_is_not_success():
    from naiyue_test_agent.envd.framing import FrameStream
    async def run():
        for count in (1,3):
            source=asyncio.StreamReader();sink=Sink()
            for i in range(count):
                source.feed_data(json.dumps(dict(generation='g',request_id=str(i),offset=0,end=False,data='{')).encode()+b'\n')
            source.feed_eof()
            stream=FrameStream(source,sink,generation='g',frame_bytes=1024,message_bytes=4096,max_pending=2)
            with pytest.raises(SandboxError):await stream.receive()
            await stream.close()
    asyncio.run(run())


def test_message_limit_checked_before_any_frame_is_sent():
    from naiyue_test_agent.envd.framing import FrameStream
    async def run():
        sink=Sink();source=asyncio.StreamReader()
        stream=FrameStream(source,sink,generation='g',frame_bytes=1024,message_bytes=64,max_pending=2)
        with pytest.raises(SandboxError,match='env_message_too_large'):await stream.send('a',{'text':'x'*100})
        assert sink.frames==[]
        await stream.close()
    asyncio.run(run())


def test_non_utf8_filename_roundtrips_without_changing_its_identity():
    from naiyue_test_agent.envd.framing import FrameStream
    async def scenario():
        name='\udcd6\udcd0'
        value={'path':'/workspace/'+name,'text':'中文'}
        source=asyncio.StreamReader();sink=Sink()
        stream=FrameStream(source,sink,generation='g',frame_bytes=1024,message_bytes=4096,max_pending=2)
        try:
            await stream.send('r',value)
            for frame in sink.frames:source.feed_data(frame)
            assert await stream.receive()==('r',value)
            assert all(len(frame)<=1024 for frame in sink.frames)
        finally:await stream.close()
    asyncio.run(scenario())


def test_invalid_unicode_in_frame_is_a_typed_protocol_error():
    from naiyue_test_agent.envd.framing import FrameStream
    async def scenario():
        source=asyncio.StreamReader()
        source.feed_data(json.dumps(dict(generation='g',request_id='r',offset=0,end=True,data='\udcff')).encode()+b'\n')
        stream=FrameStream(source,Sink(),generation='g',frame_bytes=1024,message_bytes=4096,max_pending=2)
        try:
            with pytest.raises(SandboxError,match='env_protocol_mismatch'):await stream.receive()
        finally:await stream.close()
    asyncio.run(scenario())


def test_cancel_before_first_write_drops_queued_frame():
    from naiyue_test_agent.envd.framing import FrameStream
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        class SlowSink(Sink):
            async def drain(self):entered.set();await release.wait()
        sink=SlowSink();stream=FrameStream(asyncio.StreamReader(),sink,generation='g',frame_bytes=1024,message_bytes=4096,max_pending=3)
        first=asyncio.create_task(stream.send('first',{}));await entered.wait()
        second=asyncio.create_task(stream.send('cancelled',{}))
        while stream._queue.empty():await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):await second
        release.set();await first
        await stream.send('barrier',{})
        assert [json.loads(item)['request_id'] for item in sink.frames]==['first','barrier']
        await stream.close()
    asyncio.run(run())
