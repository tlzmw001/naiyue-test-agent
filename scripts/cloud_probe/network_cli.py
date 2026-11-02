"""Run S00 loopback client fixtures or a bridge-only ingress; Ctrl+C stops."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from threading import Event
from uuid import uuid4

from scripts.cloud_probe.fixtures import NetworkFixture,Relay


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_subparsers(dest='mode',required=True)
    fixture=modes.add_parser('fixture')
    fixture.add_argument('--output',required=True,type=Path)
    bridge=modes.add_parser('bridge')
    bridge.add_argument('--output',required=True,type=Path)
    bridge.add_argument('--bind-host',required=True)
    bridge.add_argument('--http-remote-port',required=True,type=int)
    bridge.add_argument('--grpc-remote-port',required=True,type=int)
    args=parser.parse_args()
    if args.mode=='bridge':
        host=ipaddress.ip_address(args.bind_host)
        if not host.is_private or host.is_loopback or host.is_unspecified:
            parser.error('Bridge ingress must bind the dedicated private Docker gateway')
        if not all(0<port<65536 for port in (args.http_remote_port,args.grpc_remote_port)):
            parser.error('Invalid loopback reverse port')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        services=[]
        try:
            if args.mode=='fixture':
                marker=uuid4().hex
                service=NetworkFixture('127.0.0.1',marker);services.append(service)
                facts=dict(service.endpoints(),http_sha256=hashlib.sha256(marker.encode()).hexdigest())
            else:
                for port in (args.http_remote_port,args.grpc_remote_port):
                    services.append(Relay(args.bind_host,('127.0.0.1',port)))
                http,rpc=[service.address for service in services]
                facts={'http_url':f'http://{http[0]}:{http[1]}/','grpc_target':f'{rpc[0]}:{rpc[1]}'}
            facts.update(pid=os.getpid(),scope='S00 fixture only; no business data or credentials')
            text=json.dumps(facts,indent=2)
            stream.write(text+'\n');stream.flush();print(text,flush=True)
            Event().wait()
        except KeyboardInterrupt:
            print('S00 fixture stopping',flush=True)
        finally:
            for service in reversed(services):service.close()


if __name__=='__main__':main()
