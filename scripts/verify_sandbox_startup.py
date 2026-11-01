"""Production-policy startup gate; only creates and removes its own test sandbox."""
import argparse
import asyncio
import base64
import json
import logging
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4

from naiyue_test_agent.contracts.sandbox import SandboxRecord
from naiyue_test_agent.execution.docker_cli import DockerCLI
from naiyue_test_agent.execution.docker_environment import DockerEnvironment
from naiyue_test_agent.execution.sandbox_transport import SandboxTransport
from naiyue_test_agent.sandbox_profiles import SandboxConfiguration
from naiyue_test_agent.sandbox_settings import SandboxSettings
from scripts.sandbox_probe.exercise import SECURITY_CODE


def verify_report(report):
    identity=report.get('task_identity',{})
    if (any(report.get(key) is not True for key in ('hello','bash','aitest','stopped','owned_resources_removed'))
            or identity.get('uid')!=1000 or identity.get('gid')!=1000 or identity.get('groups')!=[]
            or identity.get('no_new_privs')!=1 or identity.get('caps')!={'CapEff':0,'CapPrm':0,'CapInh':0,'CapAmb':0}
            or report.get('isolation')!={'uid':1000,'journal_readwrite':False,'socket_connect':False,'controller_environ_read':False}):
        raise ValueError('Production sandbox startup gate incomplete')


async def scenario(directory,settings,report):
    cli=DockerCLI(settings,directory);engine=DockerEnvironment(cli)
    transport=SandboxTransport(directory,SandboxConfiguration());engine.transport=transport
    daemon,image=await asyncio.to_thread(cli.preflight,settings.image)
    ident=uuid4().hex
    row=SandboxRecord(ident,'startup-fixture',uuid4().hex,'creating',daemon,
        'nyt-e-'+ident,'nyt-v-'+ident,image['Id'],settings.model_dump(mode='json'))
    saved=[row]
    def save(value):saved[0]=value
    async def request(operation,params,*,mutates=False):
        return await transport.arequest(row,operation,params,mutates=mutates)
    async def submit(kind,payload):
        operation=uuid4().hex
        await request('operation_submit',dict(operation_id=operation,kind=kind,payload=payload),mutates=True)
        return operation
    async def poll(operation):
        return await request('operation_poll',dict(operation_id=operation,offset=0,limit=65536))
    async def command(kind,payload):
        operation=await submit(kind,payload)
        async with asyncio.timeout(30):
            while True:
                current=await poll(operation);state=current['operation']
                if state['state'] not in ('starting','running'):break
                await asyncio.sleep(.05)
        assert state['state']=='completed' and state['result']['ok'] and state['result']['value']['returncode']==0,state
        return base64.b64decode(current['output']['bytes_base64'],validate=True)
    def python(code):
        return dict(args=['-I','-c',code],timeout=20,settings={'executable':'python','prefix_args':[]},process_settings=None)
    try:
        report['phase']='container';started=time.monotonic()
        row=await asyncio.to_thread(engine.create,row,save)
        report['phase']='handshake';await asyncio.to_thread(engine.handshake,row,start=settings.ssh is not None)
        report['hello']=True;report['create_and_handshake_seconds']=round(time.monotonic()-started,3)
        report['phase']='task_identity'
        report['task_identity']=json.loads(await command('argv',python("""import json,os
from pathlib import Path
s=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
print(json.dumps(dict(uid=os.getuid(),gid=os.getgid(),groups=os.getgroups(),no_new_privs=int(s['NoNewPrivs']),
caps={k:int(s[k],16) for k in ('CapEff','CapPrm','CapInh','CapAmb')})))""")))
        report['isolation']=json.loads(await command('argv',python(SECURITY_CODE)))
        report['phase']='bash_aitest'
        # /workspace is the approved alias for /work/workspace; verify inode identity.
        bash=await command('shell',dict(name='bash',command='test . -ef /workspace && echo workspace-ok; uname -s; id -u',
            timeout=10,settings={},process_settings=None))
        report['bash']=bash==b'workspace-ok\nLinux\n1000\n'
        aitest=await command('argv',dict(args=['--help'],timeout=10,settings={'executable':'aitest'},process_settings=None))
        report['aitest']=b'Usage:' in aitest and b'aitest' in aitest
        report['phase']='stop'
        operation=await submit('argv',python('import time;print("ready",flush=True);time.sleep(60)'))
        async with asyncio.timeout(10):
            while not (await poll(operation))['output']['observed_bytes']:await asyncio.sleep(.05)
        started=time.monotonic();stopped=await request('operation_stop',{'operation_id':operation},mutates=True)
        report['stop_seconds']=round(time.monotonic()-started,3)
        report['stopped']=stopped['state']=='stopped' and stopped['result']['value']['process_reaped'] is True
    finally:
        primary=sys.exc_info()[1]
        failures=[]
        try:await transport.aclose()
        except Exception as error:
            failures.append(error)
            logging.getLogger(__name__).error('startup_gate_channel_cleanup_failed error_type=%s',type(error).__name__)
        try:
            row=saved[0]
            await asyncio.to_thread(engine.action,row,'destroy')
            report['owned_resources_removed']=True
        except Exception as error:
            failures.append(error)
            logging.getLogger(__name__).error('startup_gate_cleanup_failed error_type=%s',type(error).__name__)
        if failures and primary is None:raise failures[0]
    verify_report(report)


def main():
    if sys.flags.optimize:raise RuntimeError('Acceptance requires assertions enabled')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True)
    parser.add_argument('--ssh-host');parser.add_argument('--ssh-executable',default='ssh')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Choose a fresh report path')
    settings=SandboxSettings(context=args.context,image=args.image,network={'mode':'none'},
        ssh={'host':args.ssh_host,'executable':args.ssh_executable} if args.ssh_host else None)
    report=dict(status='running',provider_requests=0,transport='ssh' if settings.ssh else 'local')
    try:
        with tempfile.TemporaryDirectory(prefix='nyt-startup-gate-') as directory:
            asyncio.run(scenario(Path(directory).resolve(),settings,report))
        report['status']='passed';report['phase']='completed'
    except Exception as error:
        report['status']='failed';report['error_type']=type(error).__name__
        if hasattr(error,'code'):report['error_code']=error.code
        raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
