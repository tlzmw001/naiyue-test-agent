"""Finite JSON fragments with fair, prioritized writes over an owned byte stream."""
import asyncio
import itertools
import json
import logging

from naiyue_test_agent.contracts.sandbox import SandboxError


class FrameStream:
    def __init__(self,reader,writer,*,generation,frame_bytes,message_bytes,max_pending,timeout=60):
        if frame_bytes<1024 or message_bytes<1 or max_pending<1:raise ValueError('Invalid frame budget')
        self.reader,self.writer=reader,writer
        self.generation=generation;self.frame_bytes=frame_bytes;self.message_bytes=message_bytes
        self.max_pending=max_pending;self.timeout=timeout
        self._partial={};self._queue=asyncio.PriorityQueue(maxsize=max_pending)
        self._sequence=itertools.count();self._writing=None;self._closed=False;self._failure=None

    async def send(self,ident,value,*,priority=1,on_first_write=None):
        raw=json.dumps(value,separators=(',',':'),ensure_ascii=False)
        if len(raw.encode('utf-8'))>self.message_bytes:raise SandboxError('env_message_too_large')
        if self._closed:raise SandboxError('env_unreachable')
        if self._failure:raise self._failure
        if self._writing is None:self._writing=asyncio.create_task(self._write())
        # Bound even worst-case JSON escaping. No message holds the writer across fragments.
        size=(self.frame_bytes-256)//12
        for offset in range(0,len(raw),size):
            if self._failure:raise self._failure
            piece=raw[offset:offset+size]
            frame=json.dumps(dict(generation=self.generation,request_id=ident,offset=offset,
                end=offset+len(piece)==len(raw),data=piece),separators=(',',':'),ensure_ascii=False).encode()+b'\n'
            if len(frame)>self.frame_bytes:raise SandboxError('env_message_too_large')
            done=asyncio.get_running_loop().create_future()
            async with asyncio.timeout(self.timeout):
                try:
                    await self._queue.put((priority,next(self._sequence),frame,
                        on_first_write if offset==0 else None,done))
                    await done
                finally:
                    if not done.done():done.cancel()

    async def _write(self):
        current=None
        try:
            while True:
                _,_,frame,on_write,current=await self._queue.get()
                if current.cancelled():current=None;continue
                if on_write is not None:on_write()
                self.writer.write(frame)
                async with asyncio.timeout(self.timeout):await self.writer.drain()
                if not current.done():current.set_result(None)
                current=None
        except BaseException as error:
            self._failure=SandboxError('env_unreachable')
            if not isinstance(error,asyncio.CancelledError):
                logging.getLogger(__name__).warning('sandbox_frame_write_failed error_type=%s',type(error).__name__)
            if current is not None and not current.done():current.set_exception(self._failure)
            while not self._queue.empty():
                *_,done=self._queue.get_nowait()
                if not done.done():done.set_exception(self._failure)
            if isinstance(error,asyncio.CancelledError):raise

    async def receive(self):
        while True:
            try:raw=await self.reader.readline()
            except (ValueError,UnicodeError) as error:raise SandboxError('env_protocol_mismatch') from error
            if not raw:
                if self._partial:raise SandboxError('env_protocol_mismatch')
                return None
            if len(raw)>self.frame_bytes or not raw.endswith(b'\n'):raise SandboxError('env_protocol_mismatch')
            try:frame=json.loads(raw)
            except (ValueError,UnicodeError) as error:raise SandboxError('env_protocol_mismatch') from error
            if (not isinstance(frame,dict) or set(frame)!={'generation','request_id','offset','end','data'}
                    or frame['generation']!=self.generation or not isinstance(frame['request_id'],str)
                    or not 1<=len(frame['request_id'])<=64 or type(frame['offset']) is not int
                    or type(frame['end']) is not bool or not isinstance(frame['data'],str) or not frame['data']):
                raise SandboxError('env_protocol_mismatch')
            ident=frame['request_id']
            if ident not in self._partial:
                if len(self._partial)>=self.max_pending:raise SandboxError('env_message_too_large')
                self._partial[ident]=[[],0,0]
            parts,offset,length=self._partial[ident]
            if frame['offset']!=offset:raise SandboxError('env_protocol_mismatch')
            piece=frame['data'];length+=len(piece.encode('utf-8'))
            if length>self.message_bytes:raise SandboxError('env_message_too_large')
            parts.append(piece);self._partial[ident]=[parts,offset+len(piece),length]
            if frame['end']:
                self._partial.pop(ident)
                try:value=json.loads(''.join(parts))
                except (ValueError,UnicodeError) as error:raise SandboxError('env_protocol_mismatch') from error
                if not isinstance(value,dict):raise SandboxError('env_protocol_mismatch')
                return ident,value

    async def close(self):
        self._closed=True
        if self._writing is not None:
            self._writing.cancel()
            await asyncio.gather(self._writing,return_exceptions=True)
        self._partial.clear()
