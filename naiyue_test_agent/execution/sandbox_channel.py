"""One authenticated persistent sandbox stream; all requests retain independent custody."""
import asyncio
import json
import logging
from uuid import uuid4

from naiyue_test_agent.contracts.sandbox import SandboxError,PROTOCOL_VERSION
from naiyue_test_agent.envd.framing import FrameStream
from .sandbox_child import spawn_child,close_child,drain

logger=logging.getLogger(__name__)
CONTROL={'hello','operation_stop','transport_hello'}


class SubmittedRequestCancelled(asyncio.CancelledError):
    """Internal custody receipt: persist this sent request before propagating cancellation."""
    def __init__(self,*,result=None,error=None):
        super().__init__('Submitted request was cancelled by its waiter')
        self.result,self.error=result,error


class SandboxChannel:
    def __init__(self,process,environment,session_id,*,timeout,frame_bytes,message_bytes,io_slots,control_slots,max_waiting):
        self.process=process;self.environment=environment;self.session_id=session_id;self.timeout=timeout
        self.frame_bytes=frame_bytes;self.message_bytes=message_bytes;self.max_waiting=max_waiting
        self.pending={};self.failed=None;self.sent_count=0;self.stderr_bytes=0
        self._io=asyncio.Semaphore(io_slots);self._control=asyncio.Semaphore(control_slots)
        self._slots=io_slots+control_slots;self._calls=set();self._reader=None;self._stderr=None
        self._queued={'io':0,'control':0};self._control_budget=control_slots
        self._close_task=None;self.frames=None

    @classmethod
    async def open(cls,argv,*,environment,session_id,env=None,cwd=None,timeout=60,
                   frame_bytes=65536,message_bytes=16777216,io_slots=8,control_slots=2,max_waiting=64,preamble_check=None):
        process=await spawn_child(argv,env=env,cwd=cwd,limit=message_bytes if preamble_check is not None else frame_bytes)
        self=cls(process,environment,session_id,timeout=timeout,frame_bytes=frame_bytes,
            message_bytes=message_bytes,io_slots=io_slots,control_slots=control_slots,max_waiting=max_waiting)
        self._stderr=asyncio.create_task(self._drain_stderr())
        try:
            if preamble_check is not None:
                async with asyncio.timeout(timeout):preamble=await process.stdout.readline()
                if len(preamble)>message_bytes:raise SandboxError('env_message_too_large')
                preamble_check(json.loads(preamble))
                process.stdin.write(b'NYT-ATTACH-OK\n')
                async with asyncio.timeout(timeout):await process.stdin.drain()
            async with asyncio.timeout(timeout):raw=await process.stdout.readline()
            if not raw and await process.wait()==2:raise SandboxError('env_attach_unavailable')
            ready=json.loads(raw)
            if (len(raw)>frame_bytes or not isinstance(ready,dict) or ready.get('attach')!=1
                    or ready.get('protocol')!=PROTOCOL_VERSION or ready.get('environment')!=environment
                    or ready.get('session_id')!=session_id or not isinstance(ready.get('generation'),str)
                    or not 1<=len(ready['generation'])<=64):
                raise SandboxError('env_protocol_mismatch')
            self.generation=ready['generation']
            self.frames=FrameStream(process.stdout,process.stdin,generation=self.generation,
                frame_bytes=frame_bytes,message_bytes=message_bytes,max_pending=self._slots,timeout=timeout)
            self._reader=asyncio.create_task(self._read())
            if self.failed:raise self.failed
            return self
        except BaseException as error:
            await self.close()
            if isinstance(error,(asyncio.CancelledError,SandboxError)):raise
            raise SandboxError('env_protocol_mismatch') from error

    def _fail(self,code):
        if self.failed is None:
            self.failed=SandboxError(code)
            logger.warning('sandbox_channel_failed binding_id=%s code=%s',self.environment['binding_id'],code)
        for future in self.pending.values():
            if not future.done():future.set_exception(self.failed)
        if self.frames is not None and self._close_task is None:
            self._close_task=asyncio.create_task(self._close())
            self._close_task.add_done_callback(self._closed)

    @staticmethod
    def _closed(task):
        if not task.cancelled() and task.exception() is not None:
            logger.error('sandbox_channel_close_failed error_type=%s',type(task.exception()).__name__)

    async def _drain_stderr(self):
        try:
            while chunk:=await self.process.stderr.read(8192):
                self.stderr_bytes+=len(chunk)
                if self.stderr_bytes>65536:self._fail('env_message_too_large')
        except OSError:self._fail('env_unreachable')

    async def _read(self):
        try:
            while item:=await self.frames.receive():
                ident,response=item
                if (ident not in self.pending or response.get('request_id')!=ident
                        or response.get('environment')!=self.environment or response.get('session_id')!=self.session_id
                        or response.get('protocol')!=PROTOCOL_VERSION or type(response.get('ok')) is not bool
                        or response['ok'] and 'result' not in response):raise SandboxError('env_protocol_mismatch')
                future=self.pending[ident]
                if future.done():raise SandboxError('env_protocol_mismatch')
                future.set_result(response)
            self._fail('env_unreachable')
        except asyncio.CancelledError:raise
        except Exception as error:self._fail(error.code if isinstance(error,SandboxError) else 'env_protocol_mismatch')

    async def request(self,operation,params,*,mutates=False):
        if self.failed:raise self.failed
        group='control' if operation in CONTROL else 'io'
        if self._queued[group]>=(self._control_budget if group=='control' else self.max_waiting):
            raise SandboxError('env_connection_capacity')
        self._queued[group]+=1
        custody={'sent':False}
        task=asyncio.create_task(self._request(operation,params,mutates,custody))
        self._calls.add(task);task.add_done_callback(lambda task:self._done(task,group))
        # Cancellation abandons only this wait. Custody drains its reply/timeout and slot.
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not custody['sent']:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
            elif mutates:
                from naiyue_test_agent.agent_runtime.async_utils import await_settled
                try:await await_settled(task)
                except BaseException:
                    # The task owns the original typed result/error; never log its body.
                    if not task.done():raise
                error=asyncio.CancelledError() if task.cancelled() else task.exception()
                raise SubmittedRequestCancelled(result=task.result() if error is None else None,error=error)
            raise

    def _done(self,task,group):
        self._calls.discard(task)
        self._queued[group]-=1
        if not task.cancelled() and task.exception() is not None:
            logger.debug('sandbox_request_finished_with_error error_type=%s',type(task.exception()).__name__)

    async def _request(self,operation,params,mutates,custody):
        ident=uuid4().hex;future=None
        try:
            async with asyncio.timeout(self.timeout),self._control if operation in CONTROL else self._io:
                if self.failed:raise self.failed
                request=dict(protocol=PROTOCOL_VERSION,request_id=ident,environment=self.environment,
                    session_id=self.session_id,operation=operation,params=params)
                if len(json.dumps(request,ensure_ascii=False,separators=(',',':')).encode())>self.message_bytes:
                    raise SandboxError('env_message_too_large')
                future=asyncio.get_running_loop().create_future();self.pending[ident]=future
                def sent():
                    custody['sent']=True;self.sent_count+=1
                await self.frames.send(ident,request,priority=0 if operation in CONTROL else 1,on_first_write=sent)
                response=await future
        except (TimeoutError,OSError) as error:
            self._fail('env_unreachable')
            raise SandboxError('env_operation_unknown' if custody['sent'] and mutates else 'env_unreachable') from error
        except SandboxError as error:
            if custody['sent'] and mutates:raise SandboxError('env_operation_unknown') from error
            raise
        finally:
            self.pending.pop(ident,None)
            if future is not None:
                if not future.done():future.cancel()
                elif not future.cancelled():future.exception()
        return decode_response(response,mutates=mutates)

    async def close(self):
        from naiyue_test_agent.agent_runtime.async_utils import await_settled
        if self._close_task is None:self._close_task=asyncio.create_task(self._close())
        await await_settled(self._close_task)

    async def _close(self):
        self._fail('env_stopped')
        if self.frames is not None:await self.frames.close()
        calls=list(self._calls)
        try:
            async with asyncio.timeout(2):await asyncio.gather(*calls,return_exceptions=True)
        except TimeoutError:
            logger.error('sandbox_request_drain_unconfirmed')
        # Drain even if the protocol reader has failed, so a full pipe cannot prevent reap.
        if self._reader is not None:
            self._reader.cancel();await asyncio.gather(self._reader,return_exceptions=True)
        draining=asyncio.create_task(drain(self.process.stdout))
        try:await close_child(self.process)
        finally:
            tasks=[task for task in (draining,self._stderr) if task is not None]
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)


def decode_response(response,*,mutates):
    if response['ok']:return response['result']
    from langchain_core.tools import ToolException
    from naiyue_test_agent.contracts.git_inputs import GIT_INPUT_CODES,GitInputError
    from naiyue_test_agent.contracts.git_results import GIT_RESULT_CODES
    detail=response.get('error')
    bad='env_operation_unknown' if mutates else 'env_protocol_mismatch'
    if not isinstance(detail,dict) or not isinstance(detail.get('code'),str) or not isinstance(detail.get('message'),str):
        raise SandboxError(bad)
    code=detail['code']
    if code in GIT_INPUT_CODES|GIT_RESULT_CODES:raise GitInputError(code)
    if code=='tool_error':raise ToolException(detail['message'][:4096])
    if code not in {'env_binding_mismatch','env_message_too_large','env_operation_unsupported',
            'env_resource_outside','env_resource_unavailable','env_operation_unknown','env_protocol_mismatch',
            'env_operation_not_found','env_operation_conflict','env_journal_full','env_operation_capacity',
            'env_invalid_operation_id','env_stop_unconfirmed','env_stopped','env_connection_capacity','env_unreachable'}:
        raise SandboxError(bad)
    raise SandboxError(code)
