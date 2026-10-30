"""Persistent stdio attachment to envd's private socket; no network listener."""
import asyncio
import errno
import json
import logging
import socket
import sys
from uuid import uuid4

from naiyue_test_agent.contracts.sandbox import SandboxError,PROTOCOL_VERSION,SOCKET_PATH
from .protocol import check_request,encode
from .framing import FrameStream

logger=logging.getLogger(__name__)
CONTROL={'hello','operation_stop','request_cancel'}
REQUEST_TIMEOUT=object()


def check_envelope(ident,request,environment,session_id):
    check_request(request,environment,session_id)
    if ident!=request['request_id']:raise SandboxError('env_binding_mismatch')


async def connected_stream(path,limit):
    loop=asyncio.get_running_loop()
    while True:
        channel=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);channel.setblocking(False)
        try:
            await loop.sock_connect(channel,path)
            try:channel.getpeername()
            except OSError as error:
                # Linux backlog EAGAIN may look writable without a connected peer.
                if error.errno!=errno.ENOTCONN:raise
                channel.close();await asyncio.sleep(.01);continue
            return await asyncio.open_unix_connection(sock=channel,limit=limit)
        except (FileNotFoundError,ConnectionRefusedError):
            # The container can be running before envd finishes initializing.
            # No bytes were sent; socket_request's outer deadline bounds readiness.
            channel.close();await asyncio.sleep(.05)
        except BaseException:
            channel.close();raise


async def socket_request(path,request,*,limit,timeout,wait_timeout=REQUEST_TIMEOUT,keep_open=False):
    kept=False
    async with asyncio.timeout(timeout):
        reader,writer=await connected_stream(path,limit)
    try:
        async with asyncio.timeout(timeout):
            writer.write(encode(request,limit));await writer.drain()
        async with asyncio.timeout(timeout if wait_timeout is REQUEST_TIMEOUT else wait_timeout):
            raw=await reader.readline()
            if not raw or len(raw)>limit or not raw.endswith(b'\n'):raise SandboxError('env_protocol_mismatch')
            try:response=json.loads(raw)
            except (ValueError,UnicodeError) as error:raise SandboxError('env_protocol_mismatch') from error
            if (not isinstance(response,dict) or type(response.get('ok')) is not bool
                    or any(response.get(key)!=request[key] for key in ('protocol','request_id','environment','session_id'))):
                raise SandboxError('env_binding_mismatch')
            if keep_open:
                kept=True;return response,writer
            return response
    finally:
        if not kept:
            writer.close()
            async with asyncio.timeout(timeout):await writer.wait_closed()


async def attach(environment,session_id,settings,*,lane='main'):
    policy=settings.transport;limit=settings.max_message_bytes;timeout=settings.rpc_timeout_seconds
    base=dict(protocol=PROTOCOL_VERSION,environment=environment.public(),session_id=session_id)
    hello=await socket_request(SOCKET_PATH,dict(base,request_id=uuid4().hex,operation='transport_hello',params={}),
        limit=limit,timeout=timeout)
    ready=hello.get('result')
    if hello['ok'] is not True or not isinstance(ready,dict) or not isinstance(ready.get('generation'),str):
        raise SandboxError('env_protocol_mismatch')
    generation=ready['generation']
    loop=asyncio.get_running_loop();reader=asyncio.StreamReader(limit=policy.frame_bytes)
    await loop.connect_read_pipe(lambda:asyncio.StreamReaderProtocol(reader),sys.stdin.buffer)
    transport,protocol=await loop.connect_write_pipe(lambda:asyncio.streams.FlowControlMixin(loop=loop),sys.stdout.buffer)
    writer=asyncio.StreamWriter(transport,protocol,None,loop)
    io_slots=1 if lane=='bulk' else policy.io_requests
    frames=FrameStream(reader,writer,generation=generation,frame_bytes=policy.frame_bytes,
        message_bytes=limit,max_pending=policy.pending_requests*2+1,timeout=timeout,
        buffer_bytes=limit*(io_slots+policy.control_requests+1))
    tasks={};active={'io':0,'control':0,'health':0};searches={}

    async def remote(request,**options):
        response=await socket_request(SOCKET_PATH,dict(request,generation=generation),limit=limit,timeout=timeout,**options)
        if response.get('generation')!=generation:raise SandboxError('env_binding_mismatch')
        return response

    async def control(operation,ident):
        return await remote(dict(base,request_id=uuid4().hex,operation=operation,params={'request_id':ident}))

    async def release(ident):
        try:await control('request_release',ident)
        except (OSError,TimeoutError,SandboxError) as error:
            logger.warning('envd_attach_search_release_unconfirmed error_type=%s',type(error).__name__)

    async def handle(ident,request,group):
        lease=None
        try:
            try:
                if ident in searches:
                    entry=searches[ident]
                    try:
                        registered,lease=await socket_request(SOCKET_PATH,dict(base,generation=generation,
                            request_id=uuid4().hex,operation='request_register',params={'request_id':ident}),
                            limit=limit,timeout=timeout,keep_open=True)
                        if registered.get('generation')!=generation:raise SandboxError('env_binding_mismatch')
                    finally:entry.set()
                    if not registered['ok']:
                        response=dict(base,request_id=ident,ok=False,error=registered['error'])
                    else:
                        request=dict(request,params=dict(request['params'],registered=True))
                        response=await remote(request,wait_timeout=None)
                else:
                    if request['operation']=='request_cancel':
                        entry=searches.get(request['params'].get('request_id'))
                        if entry is not None:await entry.wait()
                    response=await remote(request)
            except (OSError,TimeoutError,SandboxError) as error:
                logger.warning('envd_attach_request_failed operation=%s error_type=%s',request['operation'],type(error).__name__)
                if isinstance(error,SandboxError) and error.code in ('env_binding_mismatch','env_protocol_mismatch'):raise
                # Bytes might have reached envd: controller consults the original operation journal.
                response=dict(base,request_id=ident,ok=False,error=dict(code='env_operation_unknown',message='Transport result unconfirmed'))
            written=False
            def started():
                nonlocal written
                written=True
            try:await frames.send(ident,response,priority=0 if group!='io' else 1,on_first_write=started)
            except (ValueError,TypeError,UnicodeError,SandboxError) as error:
                if written or isinstance(error,SandboxError) and error.code!='env_message_too_large':raise
                logger.warning('envd_attach_response_rejected error_type=%s',type(error).__name__)
                code=error.code if isinstance(error,SandboxError) else 'env_protocol_mismatch'
                await frames.send(ident,dict(base,request_id=ident,ok=False,
                    error=dict(code=code,message='Response cannot be represented within the protocol budget')),priority=0)
        finally:
            if lease is not None:
                lease.close()
                async with asyncio.timeout(timeout):await lease.wait_closed()
            if ident in searches:
                await release(ident);searches.pop(ident,None)
            active[group]-=1

    writer.write(encode(ready,policy.frame_bytes));await writer.drain()
    try:
        async with asyncio.TaskGroup() as workers:
            try:
                while True:
                    async with asyncio.timeout(policy.keepalive_interval_seconds*policy.keepalive_count+policy.heartbeat_timeout_seconds):
                        item=await frames.receive()
                    if item is None:break
                    ident,request=item;check_envelope(ident,request,environment,session_id)
                    tasks={key:task for key,task in tasks.items() if not task.done()}
                    if ident in tasks:raise SandboxError('env_protocol_mismatch')
                    op=request['operation']
                    group='health' if op=='transport_hello' else 'control' if op in CONTROL else 'io'
                    if active[group]>=(1 if group=='health' else policy.control_requests if group=='control' else io_slots):
                        await frames.send(ident,dict(base,request_id=ident,ok=False,
                            error=dict(code='env_connection_capacity',message='Connection request capacity reached')),priority=0)
                        continue
                    active[group]+=1
                    if op in ('find','grep'):searches[ident]=asyncio.Event()
                    tasks[ident]=workers.create_task(handle(ident,request,group))
            finally:
                async with asyncio.timeout(policy.heartbeat_timeout_seconds):
                    await asyncio.gather(*(release(ident) for ident in tuple(searches)))
                for task in tasks.values():task.cancel()
    except Exception as error:
        logger.error('envd_attach_failed error_type=%s',type(error).__name__)
        raise SandboxError('env_unreachable') from None
    finally:
        await frames.close();writer.close()
