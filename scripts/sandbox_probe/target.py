"""Explicit target selection and fixed subprocess construction for probes."""
import base64
from dataclasses import dataclass
import os
from pathlib import Path,PurePosixPath
import re
import shlex
import zlib
import json
from dataclasses import replace

ENV_KEYS={'PATH','HOME','USERPROFILE','SYSTEMROOT','PROGRAMDATA','TMPDIR','TEMP','TMP','SSH_AUTH_SOCK','DOCKER_CONFIG'}


def child_environment(source=None):
    return {key:value for key,value in (os.environ if source is None else source).items() if key.upper() in ENV_KEYS}


@dataclass(frozen=True)
class Target:
    container: str
    ssh_target: str|None=None
    context: str='default'
    ssh_executable: str='ssh'
    docker_executable: str='docker'
    socket_path: str='/var/run/docker.sock'

    def __post_init__(self):
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',self.container):raise ValueError('invalid container')
        if self.ssh_target is not None:
            if not re.fullmatch(r'\w[\w.-]{0,254}',self.ssh_target):raise ValueError('invalid SSH alias')
            if self.context!='default':raise ValueError('SSH and Docker context are mutually exclusive')
        if not self.context or self.context.startswith('-') or any(c.isspace() for c in self.context):raise ValueError('invalid context')
        path=PurePosixPath(self.socket_path)
        if not path.is_absolute() or '..' in path.parts or '\\' in self.socket_path:raise ValueError('invalid socket')
        for value in (self.ssh_executable,self.docker_executable,self.socket_path):
            if not value or value.startswith('-') or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('invalid executable or socket')

    def command(self,args):
        if self.ssh_target is None:return [self.docker_executable,'--context',self.context,*args]
        remote='exec '+shlex.join([self.docker_executable,'--host','unix://'+self.socket_path,*args])
        return [self.ssh_executable,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
            '-o','ForwardAgent=no','-o','ClearAllForwardings=yes','-o','Compression=no',
            '-o','ConnectTimeout=15','--',self.ssh_target,remote]

    def worker_command(self,*,owned=False):
        raw=Path(__file__).with_name('worker.py').read_bytes()
        blob=base64.b64encode(zlib.compress(raw)).decode()
        code="import base64,zlib;exec(compile(zlib.decompress(base64.b64decode('"+blob+"')),'<nyt-probe>','exec'))"
        return self.command(['container','exec','-i',self.container,'python','-I','-u','-c',code,
                             'owned' if owned else 'readonly'])


def validate_container(data,*,owned=False):
    if not isinstance(data,dict):raise ValueError('invalid container response')
    labels=data.get('Config',{}).get('Labels') or {}
    if (not data.get('State',{}).get('Running') or not all(labels.get('naiyue.'+key) for key in ('binding','session','project'))):
        raise ValueError('target is not a running project sandbox')
    if owned and labels.get('naiyue.probe')!='spec141':raise ValueError('target is not a dedicated probe sandbox')
    identity=data.get('Id')
    if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{64}',identity):raise ValueError('invalid container identity')
    return labels


async def inspect_target(target,*,owned=False):
    from .transport import run_command,ProbeError
    raw=await run_command(target.command(['container','inspect',target.container]))
    try:
        rows=json.loads(raw)
        if not isinstance(rows,list) or len(rows)!=1:raise ValueError('invalid inspection')
        data=rows[0];labels=validate_container(data,owned=owned)
        environment=dict(item.split('=',1) for item in data['Config']['Env'] if '=' in item)
        expected=json.loads(environment['NYT_ENVIRONMENT']);session_id=environment['NYT_SESSION_ID']
        if (expected.get('binding_id')!=labels['naiyue.binding'] or expected.get('project_id')!=labels['naiyue.project']
            or expected.get('kind')!='sandbox' or expected.get('platform')!='linux'
            or expected.get('root')!='/workspace' or session_id!=labels['naiyue.session']):
            raise ValueError('identity mismatch')
        return replace(target,container=data['Id']),expected,session_id
    except (KeyError,ValueError,TypeError):raise ProbeError('probe_target_rejected') from None
