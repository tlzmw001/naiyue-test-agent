"""Standalone code executed inside a disposable non-root task container."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import urllib.request


def main(args):
    kind=args['kind']
    if kind=='identity':
        status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
        return dict(uid=os.getuid(),gid=os.getgid(),groups=os.getgroups(),
            no_new_privs=int(status['NoNewPrivs']),
            caps={key:int(status[key],16) for key in ('CapEff','CapPrm','CapInh','CapAmb','CapBnd')})
    if kind=='seed':
        for root in ('/workspace','/state'):Path(root,'marker').write_text(args['marker'])
        try:Path('/input/injected').write_text('forbidden')
        except OSError:readonly=True
        else:readonly=False
        return {'input_readonly':readonly,'input_value':Path('/input/marker').read_text()}
    if kind=='read':
        return {root:Path(root,'marker').read_text() for root in ('/workspace','/state')}
    if kind=='boundary':
        found=[]
        for path in args['forbidden_paths']:
            try:
                with open(path,'rb') as source:source.read(1)
            except OSError:found.append(False)
            else:found.append(True)
        connected=[]
        for path in args['socket_paths']:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                try:client.connect(path)
                except OSError:connected.append(False)
                else:connected.append(True)
        return {'readable':found,'socket_connected':connected,
                'root_key_env':any(key in os.environ for key in args['credential_names'])}
    if kind=='listen':
        with socket.socket() as listener:
            listener.bind(('0.0.0.0',0));listener.listen()
            Path('/state/peer-port').write_text(str(listener.getsockname()[1]))
            while True:
                connection,_=listener.accept();connection.close()
    if kind=='port':
        path=Path('/state/peer-port')
        return {'port':int(path.read_text()) if path.exists() else None}
    if kind=='tcp':
        try:
            with socket.create_connection((args['host'],args['port']),timeout=args['timeout']):pass
        except OSError:return {'connected':False}
        return {'connected':True}
    if kind=='network':
        import grpc
        from google.protobuf.wrappers_pb2 import StringValue
        result={}
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(args['http_url'],timeout=args['timeout']) as response:
                result['http']=hashlib.sha256(response.read(4096)).hexdigest()==args['http_sha256']
        except OSError:result['http']=False
        with grpc.insecure_channel(args['grpc_target']) as channel:
            options=dict(request_serializer=StringValue.SerializeToString,response_deserializer=StringValue.FromString)
            try:
                response=channel.unary_unary('/s00.Echo/Unary',**options)(StringValue(value='s00'),timeout=args['timeout'])
                result['grpc_unary']=response.value=='s00'
            except grpc.RpcError:result['grpc_unary']=False
            try:
                response=channel.stream_stream('/s00.Echo/Bidi',**options)(iter([StringValue(value='one'),StringValue(value='two')]),timeout=args['timeout'])
                result['grpc_bidi']=[item.value for item in response]==['one','two']
            except grpc.RpcError:result['grpc_bidi']=False
        return result
    raise ValueError('unknown_s00_task_operation')


if __name__=='__main__':
    print(json.dumps(main(json.loads(sys.argv[1]))),flush=True)
