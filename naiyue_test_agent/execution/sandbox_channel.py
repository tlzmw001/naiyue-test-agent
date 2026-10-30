"""One authenticated persistent sandbox stream; all requests retain independent custody."""
import asyncio
import json
import logging
from uuid import uuid4

from naiyue_test_agent.contracts.sandbox import SandboxError,PROTOCOL_VERSION
from naiyue_test_agent.envd.framing import FrameStream,message_text
from .sandbox_child import spawn_child,close_child,drain

logger=logging.getLogger(__name__)
CONTROL={'hello','operation_stop','request_cancel'}


class RequestNotSent(SandboxError):
    """Known pre-execution rejection; never convert it into uncertain side effects."""


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
        self.retired={};self._receiving={'io':set(),'control':set(),'health':set()}
        self._queued['health']=0;self._health=asyncio.Semaphore(1)
        self._cancellations=set();self._heartbeat=None;self._probe_task=None
        self.last_response=asyncio.get_running_loop().time()
        self.heartbeat_interval=15;self.heartbeat_timeout=5

    @classmethod
    async def open(cls,argv,*,environment,session_id,env=None,cwd=None,timeout=60,
                   frame_bytes=65536,message_bytes=16777216,io_slots=8,control_slots=2,max_waiting=64,preamble_check=None,
                   cleanup_timeout=10,cleanup_attempts=2,heartbeat_interval=15,heartbeat_timeout=5):
        process=await spawn_child(argv,env=env,cwd=cwd,limit=message_bytes if preamble_check is not None else frame_bytes)
        self=cls(process,environment,session_id,timeout=timeout,frame_bytes=frame_bytes,
            message_bytes=message_bytes,io_slots=io_slots,control_slots=control_slots,max_waiting=max_waiting)
        self.cleanup_timeout=cleanup_timeout;self.cleanup_attempts=cleanup_attempts
        self.heartbeat_interval=heartbeat_interval;self.heartbeat_timeout=heartbeat_timeout
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
                frame_bytes=frame_bytes,message_bytes=message_bytes,max_pending=max_waiting*2+1,timeout=timeout,
                buffer_bytes=message_bytes*(self._slots+1))
            self._reader=asyncio.create_task(self._read())
            self._heartbeat=asyncio.create_task(self._maintain())
            if self.failed:raise self.failed
            return self
        except BaseException as error:
            try:await self.close()
            except SandboxError as cleanup:
                cleanup.channel=self
                raise
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
                if (response.get('request_id')!=ident
                        or response.get('environment')!=self.environment or response.get('session_id')!=self.session_id
                        or response.get('protocol')!=PROTOCOL_VERSION or type(response.get('ok')) is not bool
                        or response['ok'] and 'result' not in response):raise SandboxError('env_protocol_mismatch')
                self.last_response=asyncio.get_running_loop().time()
                if ident in self.retired:
                    group=self.retired.pop(ident);self._receiving[group].discard(ident)
                    continue
                if ident not in self.pending:raise SandboxError('env_protocol_mismatch')
                future=self.pending[ident]
                if future.done():raise SandboxError('env_protocol_mismatch')
                future.set_result(response)
            self._fail('env_unreachable')
        except asyncio.CancelledError:raise
        except Exception as error:self._fail(error.code if isinstance(error,SandboxError) else 'env_protocol_mismatch')

    async def request(self,operation,params,*,mutates=False):
        if self.failed:raise RequestNotSent(self.failed.code)
        if mutates and asyncio.get_running_loop().time()-self.last_response>self.heartbeat_interval*2:
            try:await self._probe()
            except SandboxError as error:raise RequestNotSent(error.code) from error
        group='health' if operation=='transport_hello' else 'control' if operation in CONTROL else 'io'
        if self._queued[group]>=(1 if group=='health' else self.max_waiting):
            raise RequestNotSent('env_connection_capacity')
        self._queued[group]+=1
        custody={'sent':False,'complete':False,'abandoned':False}
        task=asyncio.create_task(self._request(operation,params,mutates,custody))
        self._calls.add(task);task.add_done_callback(lambda task:self._done(task,group))
        # Cancellation abandons only this wait. Custody drains its reply/timeout and slot.
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not custody['sent']:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
            elif not mutates:
                custody['abandoned']=True
                if custody['complete']:
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
        ident=uuid4().hex;future=None;acquired=False
        group='health' if operation=='transport_hello' else 'control' if operation in CONTROL else 'io'
        semaphore=self._health if group=='health' else self._control if group=='control' else self._io
        try:
            async with asyncio.timeout(self.timeout):await semaphore.acquire()
            acquired=True
            if self.failed:raise self.failed
            if len(self._receiving[group])>=(1 if group=='health' else self.max_waiting):
                raise RequestNotSent('env_connection_capacity')
            request=dict(protocol=PROTOCOL_VERSION,request_id=ident,environment=self.environment,
                session_id=self.session_id,operation=operation,params=params)
            message_text(request,self.message_bytes)
            self._receiving[group].add(ident)
            future=asyncio.get_running_loop().create_future();self.pending[ident]=future
            def sent():
                custody['sent']=True;self.sent_count+=1
            async with asyncio.timeout(self.timeout):
                await self.frames.send(ident,request,priority=0 if group!='io' else 1,on_first_write=sent)
            custody['complete']=True
            if custody['abandoned']:
                self._retire(ident,group,future,operation)
                raise asyncio.CancelledError
            waiting=self.heartbeat_timeout if group=='health' else self.timeout
            if operation in ('find','grep'):
                waiting=params.get('timeout')
                waiting=waiting+self.timeout if waiting else None
            async with asyncio.timeout(waiting):response=await asyncio.shield(future)
        except TimeoutError as error:
            if not custody['sent']:raise RequestNotSent('env_request_timeout') from error
            if custody['complete'] and group!='health':
                self._retire(ident,group,future,operation)
                raise SandboxError('env_operation_unknown' if mutates else 'env_request_timeout') from error
            self._fail('env_unreachable')
            raise SandboxError('env_operation_unknown' if custody['sent'] and mutates else 'env_unreachable') from error
        except asyncio.CancelledError:
            if custody['complete']:self._retire(ident,group,future,operation)
            elif custody['sent']:self._fail('env_unreachable')
            raise
        except OSError as error:
            self._fail('env_unreachable')
            raise SandboxError('env_operation_unknown' if custody['sent'] and mutates else 'env_unreachable') from error
        except SandboxError as error:
            if custody['sent'] and mutates:raise SandboxError('env_operation_unknown') from error
            if not custody['sent']:raise RequestNotSent(error.code) from error
            raise
        finally:
            self.pending.pop(ident,None)
            if ident not in self.retired:self._receiving[group].discard(ident)
            if acquired:semaphore.release()
            if future is not None:
                if not future.done():future.cancel()
                elif not future.cancelled():future.exception()
        return decode_response(response,mutates=mutates)

    def _retire(self,ident,group,future,operation):
        if self.failed or future is None or future.done():return
        self.retired[ident]=group
        if operation in ('find','grep'):
            task=asyncio.create_task(self._cancel_remote(ident))
            self._cancellations.add(task);task.add_done_callback(self._cancellations.discard)

    async def _cancel_remote(self,ident):
        try:await self.request('request_cancel',{'request_id':ident})
        except SandboxError as error:
            logger.warning('sandbox_request_cancel_unconfirmed binding_id=%s code=%s',self.environment['binding_id'],error.code)

    async def _probe(self):
        async def check():
            result=await self.request('transport_hello',{})
            if result.get('generation')!=self.generation:
                self._fail('env_binding_mismatch');raise SandboxError('env_binding_mismatch')
        if self._probe_task is None or self._probe_task.done():self._probe_task=asyncio.create_task(check())
        return await asyncio.shield(self._probe_task)

    async def _maintain(self):
        try:
            while not self.failed:
                await asyncio.sleep(self.heartbeat_interval)
                if asyncio.get_running_loop().time()-self.last_response>=self.heartbeat_interval:await self._probe()
        except asyncio.CancelledError:raise
        except Exception:self._fail('env_unreachable')

    async def close(self,*,deadline=None):
        self.cleanup_deadline=deadline
        if (self._close_task is None or self._close_task.done()
                and (self._close_task.cancelled() or self._close_task.exception() is not None)):
            self._close_task=asyncio.create_task(self._close())
            self._close_task.add_done_callback(self._closed)
        await asyncio.shield(self._close_task)

    async def _close(self):
        deadline=getattr(self,'cleanup_deadline',None) or asyncio.get_running_loop().time()+getattr(self,'cleanup_timeout',10)
        self._fail('env_stopped')
        background=[task for task in (self._heartbeat,self._probe_task,*self._cancellations) if task is not None]
        for task in background:task.cancel()
        await asyncio.gather(*background,return_exceptions=True)
        if self.frames is not None:await self.frames.close()
        calls=list(self._calls)
        try:
            async with asyncio.timeout(max(.001,min(2,deadline-asyncio.get_running_loop().time()))):
                await asyncio.gather(*calls,return_exceptions=True)
        except TimeoutError:
            logger.error('sandbox_request_drain_unconfirmed')
        # Drain even if the protocol reader has failed, so a full pipe cannot prevent reap.
        if self._reader is not None:
            self._reader.cancel();await asyncio.gather(self._reader,return_exceptions=True)
        self.retired.clear()
        for receiving in self._receiving.values():receiving.clear()
        draining=asyncio.create_task(drain(self.process.stdout))
        if self._stderr is None or self._stderr.done():self._stderr=asyncio.create_task(self._drain_stderr())
        timeout=getattr(self,'cleanup_timeout',10)
        if deadline is not None:timeout=max(.001,min(timeout,deadline-asyncio.get_running_loop().time()))
        try:await close_child(self.process,timeout=timeout,attempts=getattr(self,'cleanup_attempts',2),readers=(draining,self._stderr))
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
    if code in {'env_connection_capacity','env_operation_capacity','env_journal_full','env_operation_unsupported'}:
        raise RequestNotSent(code)
    if code in GIT_INPUT_CODES|GIT_RESULT_CODES:raise GitInputError(code)
    if code=='tool_error':raise ToolException(detail['message'][:4096])
    if code not in {'env_binding_mismatch','env_message_too_large','env_operation_unsupported',
            'env_resource_outside','env_resource_unavailable','env_operation_unknown','env_protocol_mismatch',
            'env_operation_not_found','env_operation_conflict','env_journal_full','env_operation_capacity',
            'env_invalid_operation_id','env_stop_unconfirmed','env_stopped','env_connection_capacity','env_unreachable','env_request_timeout'}:
        raise SandboxError(bad)
    raise SandboxError(code)
