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
CONTROL={'hello','operation_stop','transport_hello'}


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


async def socket_request(path,request,*,limit,timeout):
    async with asyncio.timeout(timeout):
        reader,writer=await connected_stream(path,limit)
        try:
            writer.write(encode(request,limit));await writer.drain()
            raw=await reader.readline()
            if not raw or len(raw)>limit or not raw.endswith(b'\n'):raise SandboxError('env_protocol_mismatch')
            try:response=json.loads(raw)
            except (ValueError,UnicodeError) as error:raise SandboxError('env_protocol_mismatch') from error
            if (not isinstance(response,dict) or type(response.get('ok')) is not bool
                    or any(response.get(key)!=request[key] for key in ('protocol','request_id','environment','session_id'))):
                raise SandboxError('env_binding_mismatch')
            return response
        finally:
            writer.close();await writer.wait_closed()


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
        message_bytes=limit,max_pending=io_slots+policy.control_requests,timeout=timeout)
    tasks={};active={'io':0,'control':0}

    async def handle(ident,request,group):
        try:
            try:
                response=await socket_request(SOCKET_PATH,dict(request,generation=generation),limit=limit,timeout=timeout)
                if response.get('generation')!=generation:raise SandboxError('env_binding_mismatch')
            except (OSError,TimeoutError,SandboxError) as error:
                logger.warning('envd_attach_request_failed operation=%s error_type=%s',request['operation'],type(error).__name__)
                if isinstance(error,SandboxError) and error.code in ('env_binding_mismatch','env_protocol_mismatch'):raise
                # Bytes might have reached envd: controller consults the original operation journal.
                response=dict(base,request_id=ident,ok=False,error=dict(code='env_operation_unknown',message='Transport result unconfirmed'))
            await frames.send(ident,response,priority=0 if group=='control' else 1)
        finally:active[group]-=1

    writer.write(encode(ready,policy.frame_bytes));await writer.drain()
    try:
        async with asyncio.TaskGroup() as workers:
            try:
                while item:=await frames.receive():
                    ident,request=item;check_envelope(ident,request,environment,session_id)
                    tasks={key:task for key,task in tasks.items() if not task.done()}
                    if ident in tasks:raise SandboxError('env_protocol_mismatch')
                    group='control' if request['operation'] in CONTROL else 'io'
                    if active[group]>=(policy.control_requests if group=='control' else io_slots):
                        await frames.send(ident,dict(base,request_id=ident,ok=False,
                            error=dict(code='env_connection_capacity',message='Connection request capacity reached')),priority=0)
                        continue
                    active[group]+=1
                    tasks[ident]=workers.create_task(handle(ident,request,group))
            finally:
                for task in tasks.values():task.cancel()
    except Exception as error:
        logger.error('envd_attach_failed error_type=%s',type(error).__name__)
        raise SandboxError('env_unreachable') from None
    finally:
        await frames.close();writer.close()
