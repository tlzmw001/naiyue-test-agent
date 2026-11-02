"""Generate a NEW S00 config/build directory; installs or deploys nothing."""
import argparse
import json
import os
from pathlib import Path,PurePosixPath
import re
import secrets


def prepare(output,args):
    for name in ('network','tools_image','execd_image'):
        pattern=r'[A-Za-z0-9][A-Za-z0-9_.-]{0,200}' if name=='network' else r'[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,200}'
        if not re.fullmatch(pattern,getattr(args,name)):
            raise ValueError('invalid_'+name)
    if not re.fullmatch(r'[a-z0-9][a-z0-9._/:-]*@sha256:[a-f0-9]{64}',args.base_image):
        raise ValueError('base_image_digest_required')
    root=PurePosixPath(args.fixture_root)
    if not root.is_absolute() or '..' in root.parts or len(root.parts)<3:
        raise ValueError('dedicated_absolute_fixture_root_required')
    if not 0<args.port<65536 or min(args.uid,args.gid,args.pids)<=0:
        raise ValueError('invalid_port_or_identity_limits')
    output=Path(output)
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    secret=secrets.token_urlsafe(32)
    quote=lambda value:json.dumps(value,ensure_ascii=True)
    config=f'''[server]
host = "127.0.0.1"
port = {args.port}
api_key = {quote(secret)}
[runtime]
type = "docker"
execd_image = {quote(args.execd_image)}
[storage]
allowed_host_paths = [{quote(str(root))}]
[store]
type = "sqlite"
path = {quote(str(root/'server.sqlite'))}
[docker]
network_mode = {quote(args.network)}
publish_host = "127.0.0.1"
drop_capabilities = ["ALL"]
no_new_privileges = true
pids_limit = {args.pids}
[proxy]
resolve_internal = true
[ingress]
mode = "direct"
'''
    for name,text in [('server.toml',config),('private.credentials','NYT_S00_API_KEY='+secret+'\n')]:
        descriptor=os.open(output/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:stream.write(text)
    (output/'Dockerfile.tools').write_text(f'FROM {args.base_image}\nCOPY packages/ /wheels/\n'
        'RUN python -m pip install --no-cache-dir -c /wheels/constraints.txt /wheels/*.whl\n'
        'ENV PYTHONUNBUFFERED=1\n',encoding='utf-8')
    (output/'Dockerfile.task').write_text(f'FROM {args.tools_image}\n'
        f'RUN mkdir -p /workspace /state && chown {args.uid}:{args.gid} /workspace /state\n'
        f'USER {args.uid}:{args.gid}\nWORKDIR /workspace\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('output','network','fixture-root','base-image','tools-image','execd-image'):
        parser.add_argument('--'+field,required=True)
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--uid',type=int,default=1000)
    parser.add_argument('--gid',type=int,default=1000)
    parser.add_argument('--pids',type=int,default=256)
    args=parser.parse_args();prepare(args.output,args)
    print('Created private S00 experiment files; credentials withheld. No deployment performed.')


if __name__=='__main__':main()
