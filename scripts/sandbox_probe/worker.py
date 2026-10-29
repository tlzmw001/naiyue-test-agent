"""Disposable stdio-to-envd bridge; standalone standard-library source."""
import asyncio
import base64
import errno
import hashlib
import json
import os
import socket
import sys

READ_ONLY={'hello','resource_info','resolve_resource','list_resources','read_resource','read','prepare_target'}
CONTROL={'hello','operation_status','operation_stop'}
OWNED=READ_ONLY|CONTROL|{'operation_submit','operation_output','input_begin','input_chunk','input_cache'}
BLOCK_LIMIT=65536


def error(code):
    return {'ok':False,'error':{'code':code}}


async def dispatch(request,rpc,*,owned):
    operation=request.get('operation');params=request.get('params',{})
    if not isinstance(params,dict):return error('probe_invalid_request')
    try:
        if operation=='probe_ping':return {'ok':True,'result':{}}
        if operation=='probe_source':
            size=params.get('size')
            if type(size) is not int or not 1<=size<=BLOCK_LIMIT:return error('probe_invalid_request')
            data=os.urandom(size)
            return {'ok':True,'result':{'data':base64.b64encode(data).decode(),
                'sha256':hashlib.sha256(data).hexdigest()}}
        if operation=='probe_sink':
            value=params.get('data')
            if not isinstance(value,str) or len(value)>4*((BLOCK_LIMIT+2)//3):return error('probe_invalid_request')
            data=base64.b64decode(value,validate=True)
            if len(data)>BLOCK_LIMIT:return error('probe_invalid_request')
            return {'ok':True,'result':{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}}
        if operation not in (OWNED if owned else READ_ONLY):return error('probe_readonly')
        if operation=='prepare_target' and params.get('operation')!='read':return error('probe_readonly')
        return await rpc(request)
    except (ValueError,TypeError):
        return error('probe_invalid_request')
    except BlockingIOError:
        return error('probe_envd_backlog')
    except asyncio.TimeoutError:
        return error('probe_envd_timeout')
    except OSError as exc:
        value=error('probe_envd_io_failed')
        if type(exc.errno) is int and 0<=exc.errno<=65535:value['error']['errno']=exc.errno
        return value


async def connected_stream(socket_path,limit):
    """Wait for local admission before sending bytes; caller owns the deadline."""
    loop=asyncio.get_running_loop()
    while True:
        channel=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        channel.setblocking(False)
        try:
            await loop.sock_connect(channel,socket_path)
            try:channel.getpeername()
            except OSError as exc:
                # Linux EAGAIN can appear writable with SO_ERROR=0, still unconnected.
                if exc.errno!=errno.ENOTCONN:raise
                channel.close()
                await asyncio.sleep(.01)
                continue
            return await asyncio.open_unix_connection(sock=channel,limit=limit)
        except BaseException:
            channel.close()
            raise


async def serve(identity,session_id,socket_path,*,owned=False,limit=262144,timeout=10):
    loop=asyncio.get_running_loop()
    reader=asyncio.StreamReader(limit=limit)
    await loop.connect_read_pipe(lambda:asyncio.StreamReaderProtocol(reader),sys.stdin.buffer)
    transport,protocol=await loop.connect_write_pipe(lambda:asyncio.streams.FlowControlMixin(loop=loop),sys.stdout.buffer)
    writer=asyncio.StreamWriter(transport,protocol,None,loop)
    write_lock=asyncio.Lock();tasks=set();seen=set();active={'io':0,'control':0}
    failed=asyncio.Event();reading=None

    def completed(task):
        tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            sys.stderr.write('probe_worker_request_failed\n');failed.set()

    async def next_line():
        nonlocal reading
        reading=asyncio.create_task(reader.readline())
        broken=asyncio.create_task(failed.wait())
        try:
            await asyncio.wait((reading,broken),return_when=asyncio.FIRST_COMPLETED)
            if failed.is_set():raise ValueError('probe_worker_request_failed')
            return reading.result()
        finally:
            for task in (reading,broken):
                if not task.done():task.cancel()
            await asyncio.gather(reading,broken,return_exceptions=True)

    async def send(value):
        data=json.dumps(value,separators=(',',':'),ensure_ascii=True).encode()+b'\n'
        if len(data)>limit:raise ValueError('probe_frame_limit')
        async with write_lock:
            writer.write(data)
            async with asyncio.timeout(timeout):await writer.drain()

    async def rpc(request):
        async with asyncio.timeout(timeout):
            source,sink=await connected_stream(socket_path,limit)
            try:
                sink.write(json.dumps(request,separators=(',',':')).encode()+b'\n');await sink.drain()
                raw=await source.readline()
                if not raw or len(raw)>limit or not raw.endswith(b'\n'):raise ValueError('invalid envd frame')
                response=json.loads(raw)
                if (not isinstance(response,dict) or response.get('request_id')!=request['request_id']
                    or response.get('environment')!=identity or response.get('session_id')!=session_id
                    or response.get('protocol')!=2 or type(response.get('ok')) is not bool):
                    raise ValueError('invalid envd identity')
                return response
            finally:
                sink.close();await sink.wait_closed()

    async def handle(request,lane):
        try:
            response=await dispatch(request,rpc,owned=owned)
            response.update(protocol=2,request_id=request['request_id'],environment=identity,session_id=session_id)
            await send(response)
        finally:active[lane]-=1

    await send({'probe':'ready','version':1,'protocol':2,'environment':identity,'session_id':session_id,'owned':owned})
    try:
        while raw:=await next_line():
            if len(raw)>limit or not raw.endswith(b'\n'):raise ValueError('probe_frame_limit')
            request=json.loads(raw)
            if (not isinstance(request,dict) or set(request)!={'protocol','request_id','environment','session_id','operation','params'}
                or request.get('environment')!=identity or request.get('session_id')!=session_id or request.get('protocol')!=2
                or not isinstance(request.get('request_id'),str) or not 1<=len(request['request_id'])<=64
                or not isinstance(request.get('operation'),str) or not isinstance(request.get('params'),dict)):
                raise ValueError('probe_identity_mismatch')
            ident=request['request_id']
            if ident in seen or len(seen)>=10000:raise ValueError('probe_request_id_limit')
            seen.add(ident)
            lane='control' if request['operation'] in CONTROL else 'io'
            if active[lane]>=(2 if lane=='control' else 8):
                await send({**error('probe_capacity'),'protocol':2,'request_id':ident,
                    'environment':identity,'session_id':session_id})
                continue
            active[lane]+=1
            task=asyncio.create_task(handle(request,lane));tasks.add(task)
            task.add_done_callback(completed)
    finally:
        for task in tasks:task.cancel()
        results=await asyncio.gather(*tasks,return_exceptions=True)
        failures=[item for item in results if isinstance(item,Exception)]
        if failures:sys.stderr.write('probe_worker_request_failed\n')
        writer.close()


def main():
    # Command-line values are supplied only by the bundled controller.
    mode=sys.argv[1] if len(sys.argv)>1 else 'readonly'
    if mode not in ('readonly','owned'):raise ValueError('invalid mode')
    identity=json.loads(os.environ['NYT_ENVIRONMENT'])
    session=os.environ['NYT_SESSION_ID']
    socket_path=os.environ.get('NYT_PROBE_SOCKET','/tmp/naiyue-envd.sock')
    try:asyncio.run(serve(identity,session,socket_path,owned=mode=='owned',limit=2097152 if mode=='owned' else 262144))
    except (Exception,KeyboardInterrupt):
        sys.stderr.write('probe_worker_failed\n');raise SystemExit(2) from None


if __name__=='__main__':main()
