"""Bounded asynchronous subprocess channel for opt-in experiments."""
import asyncio
import json
import os
import signal
import subprocess
from uuid import uuid4

from .target import child_environment


class ProbeError(RuntimeError):
    """Fixed diagnostic codes only; never includes remote stderr or data."""


REMOTE_ERRORS={'probe_invalid_request','probe_readonly','probe_capacity','probe_envd_io_failed','probe_envd_backlog','probe_envd_timeout',
    'env_resource_unavailable','env_resource_outside','env_message_too_large','env_protocol_mismatch',
    'env_binding_mismatch','env_operation_not_found','env_operation_unknown','env_stop_unconfirmed',
    'env_operation_unsupported','env_stopped','tool_error','git_input_busy','git_result_busy',
    'git_input_install_unconfirmed'}


def remote_error_code(value):
    return value if isinstance(value,str) and value in REMOTE_ERRORS else 'probe_remote_error'


def process_options():
    if os.name=='nt':return {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW}
    return {'start_new_session':True}


async def run_command(argv,*,timeout=30,limit=2*1024*1024):
    """Finite inspection only; bound both streams and suppress private output."""
    process=await asyncio.create_subprocess_exec(*argv,stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,env=child_environment(),**process_options())
    async def read(stream,budget):
        result=bytearray()
        while chunk:=await stream.read(8192):
            result.extend(chunk)
            if len(result)>budget:raise ProbeError('probe_frame_limit')
        return bytes(result)
    readers=[asyncio.create_task(read(process.stdout,limit)),asyncio.create_task(read(process.stderr,65536))]
    try:
        async with asyncio.timeout(timeout):
            output,_=await asyncio.gather(*readers)
            status=await process.wait()
        if status:raise ProbeError('probe_command_failed')
        return output
    except TimeoutError:raise ProbeError('probe_timeout') from None
    finally:
        for task in readers:
            if not task.done():task.cancel()
        await asyncio.gather(*readers,return_exceptions=True)
        forced=process.returncode is None or not process.stdout.at_eof() or not process.stderr.at_eof()
        if os.name!='nt' or forced:
            try:
                if os.name=='nt':
                    if process.returncode is None:process.kill()
                else:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            await asyncio.wait_for(process.wait(),3)
            # Let pipe transports observe EOF after descendants have been killed.
            async def drain(stream):
                while await stream.read(8192):pass
            if os.name!='nt':await asyncio.wait_for(asyncio.gather(drain(process.stdout),drain(process.stderr)),3)
        if forced and os.name=='nt':raise ProbeError('probe_cleanup_unconfirmed')


class ProbeChannel:
    def __init__(self,process,expected,session_id,timeout,limit):
        self.process=process;self.expected=expected;self.session_id=session_id
        self.timeout=timeout;self.limit=limit;self.pending={};self.failed=None
        self.sent_count=0;self.received_count=0;self.sent_bytes=0;self.received_bytes=0;self.stderr_bytes=0
        self._writer=asyncio.Lock();self._io=asyncio.Semaphore(8);self._control=asyncio.Semaphore(2)
        self._reader_task=None;self._stderr_task=None;self._closed=False

    @classmethod
    async def open(cls,argv,*,expected,session_id,timeout=10,limit=262144,env=None):
        process=await asyncio.create_subprocess_exec(*argv,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,limit=limit,
            env=child_environment() if env is None else env,**process_options())
        self=cls(process,expected,session_id,timeout,limit)
        self._stderr_task=asyncio.create_task(self._stderr())
        try:
            async with asyncio.timeout(timeout):
                raw=await process.stdout.readline()
                ready=json.loads(raw)
            if (len(raw)>limit or not isinstance(ready,dict) or ready.get('probe')!='ready'
                or ready.get('version')!=1 or ready.get('protocol')!=2
                or ready.get('environment')!=expected or ready.get('session_id')!=session_id):
                raise ProbeError('probe_protocol')
            self.ready=ready
            if self.failed:raise self.failed
            self._reader_task=asyncio.create_task(self._reader())
            return self
        except BaseException as exc:
            await self.close()
            if isinstance(exc,asyncio.CancelledError):raise
            if isinstance(exc,ProbeError):raise
            raise ProbeError('probe_handshake_failed') from None

    def _fail(self,code):
        if self.failed is None:self.failed=ProbeError(code)
        for future in self.pending.values():
            if not future.done():future.set_exception(self.failed)
        self.pending.clear()

    async def _stderr(self):
        while chunk:=await self.process.stderr.read(8192):
            self.stderr_bytes+=len(chunk)
            if self.stderr_bytes>65536:self._fail('probe_stderr_limit')

    async def _reader(self):
        try:
            while raw:=await self.process.stdout.readline():
                self.received_bytes+=len(raw)
                if len(raw)>self.limit or not raw.endswith(b'\n'):raise ProbeError('probe_frame_limit')
                response=json.loads(raw)
                if (not isinstance(response,dict) or response.get('environment')!=self.expected
                    or response.get('session_id')!=self.session_id or response.get('protocol')!=2
                    or not isinstance(response.get('request_id'),str)
                    or response['request_id'] not in self.pending or type(response.get('ok')) is not bool):
                    raise ProbeError('probe_protocol')
                future=self.pending.pop(response['request_id']);self.received_count+=1
                if future.done():raise ProbeError('probe_protocol')
                future.set_result(response)
            self._fail('probe_eof')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail(str(exc) if isinstance(exc,ProbeError) else 'probe_protocol')

    async def request(self,operation,params):
        control=operation in ('hello','operation_status','operation_stop')
        slots=self._control if control else self._io
        future=None
        try:
            async with asyncio.timeout(self.timeout),slots:
                if self.failed:raise self.failed
                ident=uuid4().hex
                value=dict(protocol=2,request_id=ident,environment=self.expected,session_id=self.session_id,
                           operation=operation,params=params)
                raw=json.dumps(value,separators=(',',':'),ensure_ascii=True).encode()+b'\n'
                if len(raw)>self.limit:raise ProbeError('probe_frame_limit')
                future=asyncio.get_running_loop().create_future();self.pending[ident]=future
                async with self._writer:
                    if self.failed:raise self.failed
                    self.process.stdin.write(raw);self.sent_count+=1;self.sent_bytes+=len(raw)
                    await self.process.stdin.drain()
                response=await future
                if not response['ok']:
                    detail=response.get('error')
                    error=ProbeError(remote_error_code(detail.get('code') if isinstance(detail,dict) else None))
                    number=detail.get('errno') if isinstance(detail,dict) else None
                    error.safe_errno=number if type(number) is int and 0<=number<=65535 else None
                    raise error
                if 'result' not in response:raise ProbeError('probe_protocol')
                return response['result']
        except TimeoutError:
            self._fail('probe_timeout');raise ProbeError('probe_timeout') from None
        except asyncio.CancelledError:
            self._fail('probe_cancelled');raise
        except (BrokenPipeError,ConnectionResetError):
            self._fail('probe_eof');raise ProbeError('probe_eof') from None
        finally:
            if future is not None and not future.done():future.cancel()

    async def close(self):
        if self._closed:return
        self._closed=True;self._fail('probe_closed')
        if self.process.stdin:self.process.stdin.close()
        forced=False
        try:
            await asyncio.wait_for(self.process.wait(),2)
        except TimeoutError:
            forced=True
            try:
                if os.name=='nt':self.process.terminate()
                else:os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:await asyncio.wait_for(self.process.wait(),2)
            except TimeoutError:
                try:
                    if os.name=='nt':self.process.kill()
                    else:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await asyncio.wait_for(self.process.wait(),2)
        finally:
            # A launcher may exit before children which still hold its pipes.
            if os.name!='nt':
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            tasks=[task for task in (self._reader_task,self._stderr_task) if task is not None]
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            async def drain(stream):
                while await stream.read(8192):pass
            try:
                await asyncio.wait_for(asyncio.gather(drain(self.process.stdout),drain(self.process.stderr)),2)
            except TimeoutError:raise ProbeError('probe_cleanup_unconfirmed') from None
        # Forced Windows termination cannot prove ProxyJump child cleanup.
        if forced and os.name=='nt':raise ProbeError('probe_cleanup_unconfirmed')

    async def __aenter__(self):return self

    async def __aexit__(self,*_):await self.close()
