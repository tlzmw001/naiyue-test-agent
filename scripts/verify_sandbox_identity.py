"""Real identity/file/Git gate using only a newly created disposable sandbox."""
import argparse,asyncio,json,tempfile
from pathlib import Path
from uuid import uuid4

from naiyue_test_agent.contracts.sandbox import SandboxRecord
from naiyue_test_agent.sandbox_settings import SandboxSettings
from naiyue_test_agent.execution.docker_cli import DockerCLI
from naiyue_test_agent.execution.docker_environment import DockerEnvironment
from scripts.sandbox_probe.target import Target
from scripts.sandbox_probe.transport import ProbeChannel,ProbeError
from scripts.sandbox_probe.exercise import command,terminal,exercise,output_bytes
from scripts.sandbox_probe.fixtures import snapshot_fixture,install_fixture
from scripts.sandbox_probe.measure import latency_samples,distribution

async def scenario(directory,context,image,report):
    policy=SandboxSettings(context=context,image=image,network={'mode':'none'})
    cli=DockerCLI(policy,directory);engine=DockerEnvironment(cli)
    daemon=await asyncio.to_thread(cli.daemon)
    image_id=(await asyncio.to_thread(cli.json,['image','inspect',image]))[0]['Id']
    ident=uuid4().hex
    row=SandboxRecord(ident,'identity-fixture',uuid4().hex,'preparing',daemon,
        'nyt-e-'+ident,'nyt-v-'+ident,image_id,policy.model_dump(mode='json'))
    saved=[row]
    def save(value):saved[0]=value
    try:
        row=await asyncio.to_thread(engine.create,row,save)
        await asyncio.to_thread(engine.handshake,row)
        target=Target(row.container_id,context=context)
        async def connect():
            return await ProbeChannel.open(target.worker_command(owned=True),expected=row.environment.public(),
                session_id=row.session_id,timeout=30,limit=2097152)
        channel=await connect()
        try:
            snapshot,bundle=await asyncio.to_thread(snapshot_fixture,directory,report)
            await install_fixture(channel,snapshot,bundle,report)
            print('installed fixture',flush=True)
            facts=json.loads(await command(channel,"""import json,os
from pathlib import Path
p=Path('/workspace/private.txt');p.write_text('private-workspace');p.chmod(0o600)
s=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'caps':{k:int(s[k],16) for k in ['CapEff','CapPrm','CapInh','CapAmb']},'no_new_privs':int(s['NoNewPrivs'])}))"""))
            assert facts['uid']==facts['gid']==1000 and not any(facts['caps'].values()) and facts['no_new_privs']==1,facts
            report['task_identity']=facts
            boundary=json.loads(await command(channel,"""import json,os,signal,subprocess,sys
from pathlib import Path
checks={}
for name,action in [('restore_uid',lambda:os.setuid(0)),('restore_gid',lambda:os.setgid(0)),
    ('signal_controller',lambda:os.kill(1,0)),
    ('replace_socket',lambda:Path('/tmp/naiyue-envd.sock').unlink()),
    ('change_state',lambda:(Path('/state')/'injected').write_text('bad')),
    ('change_stage',lambda:(Path('/work/.envd-stage')/'injected').write_text('bad')),
    ('move_workspace_root',lambda:Path('/work/workspace').rename('/work/moved'))]:
    try:action()
    except PermissionError:checks[name]=False
    else:checks[name]=True
checks['supplementary_groups']=os.getgroups()
checks['control_environment']=any(key.startswith('NYT_') for key in os.environ)
checks['control_fd']=any('/state/' in os.readlink(p) or 'envd-stage' in os.readlink(p)
    for p in Path('/proc/self/fd').iterdir() if p.exists())
child=subprocess.check_output([sys.executable,'-I','-c','import os,json;print(json.dumps([os.getuid(),os.getgid(),os.getgroups()]))'])
checks['child_identity']=json.loads(child)
print(json.dumps(checks))"""))
            assert boundary.pop('child_identity')==[1000,1000,[]],boundary
            assert boundary.pop('supplementary_groups')==[],boundary
            assert all(value is False for value in boundary.values()),boundary
            report['task_boundary']=boundary
            assert output_bytes(await channel.request('read_resource',{'path':'/workspace/private.txt','max_bytes':100}))==b'private-workspace'
            subject=await asyncio.to_thread(engine.request,row,'prepare_target',{'path':'/workspace/private.txt','operation':'write'})
            subject.update(args={'path':'private.txt'},path_field='path',tool_name='write')
            operation=uuid4().hex
            await channel.request('operation_submit',{'operation_id':operation,'kind':'write','payload':{
                'subject':subject,'content':'envd-write','settings':{},'read_allowed':True}})
            state=await terminal(channel,operation)
            assert state['state']=='completed',state
            facts=json.loads(await command(channel,"""import json,os,stat
from pathlib import Path
p=Path('/workspace/private.txt');s=p.stat();p.write_text('task-write')
print(json.dumps({'uid':s.st_uid,'gid':s.st_gid,'mode':stat.S_IMODE(s.st_mode),'writable':p.read_text()=='task-write'}))"""))
            assert facts=={'uid':1000,'gid':1000,'mode':0o600,'writable':True},facts
            report['private_file_access_and_ownership']=facts
            await command(channel,"from pathlib import Path;Path('/workspace/alias').symlink_to('private.txt');Path('/workspace/escape').symlink_to('/state/journal.sqlite')")
            assert output_bytes(await channel.request('read_resource',{'path':'/workspace/alias','max_bytes':100}))==b'task-write'
            try:await channel.request('read_resource',{'path':'/workspace/escape','max_bytes':100})
            except ProbeError:report['escape_link_rejected']=True
            else:raise AssertionError('control file escaped')
            await command(channel,"from pathlib import Path;Path('/workspace/alias').unlink();Path('/workspace/escape').unlink()")
            report['small_read']=distribution(await latency_samples(channel,'read_resource',
                {'path':'/workspace/private.txt','max_bytes':100},samples=40,concurrency=8))
            from naiyue_test_agent.tools.permission_rules import PermissionSettings
            for name,operation,extra in (
                    ('find','find',dict(pattern='private.txt',limit=10,process_settings=None,timeout=10)),
                    ('grep','search',dict(pattern='task-write',glob=None,ignore_case=False,literal=True,
                        context_lines=0,limit=10,process_settings=None,timeout=10))):
                subject=await asyncio.to_thread(engine.request,row,'prepare_target',{'path':'.','operation':operation})
                subject.update(args={'path':'.'},path_field='path',tool_name=name,execution_environment=row.environment.public())
                permit=dict(session_id=row.session_id,agent_path='root',call_id='C',subject=subject)
                access=dict(permit=permit,permission_rules=PermissionSettings().model_dump(mode='json'),
                    session_allow=[],permission_mode='NoAsk')
                found=await asyncio.to_thread(engine.request,row,name,dict(subject=subject,access=access,settings={},**extra))
                assert 'private.txt' in found,found
            report['native_find_grep']='passed'
            await command(channel,"""from pathlib import Path
p=Path('/workspace/.git/fsmonitor-probe');p.write_text('#!/bin/sh\\nprintf invoked > /workspace/git-hook-ran\\n');p.chmod(0o700)
with Path('/workspace/.git/config').open('a') as f:f.write('\\n[core]\\nfsmonitor = /workspace/.git/fsmonitor-probe\\n')""")
            result=uuid4().hex
            await channel.request('operation_submit',{'operation_id':result,'kind':'git_result','payload':{
                'result_id':result,'input_id':snapshot.input_id,'original_head':snapshot.original_head,
                'input_snapshot':snapshot.input_snapshot,'include_untracked':['private.txt']}})
            state=await terminal(channel,result,timeout=120)
            assert state['state']=='completed',state
            report['git_result']=dict(completed=True,bundle_bytes=state['result']['value']['bundle_size'])
            assert await command(channel,"from pathlib import Path;assert not Path('/workspace/git-hook-ran').exists();print('safe')")==b'safe\n'
            report['workspace_git_hook_disabled']=True
        finally:await channel.close()
        report['output_retention_limit_bytes']=policy.process_output_bytes
        await exercise(connect,report)
        assert report['exercise']['workload_isolation']['passed'],report['exercise']
        assert all(x['ok'] for x in report['exercise']['capabilities'].values())
        report['image_id']=image_id
    finally:
        row=saved[0]
        if row.container_id is None:
            data=await asyncio.to_thread(cli.container,row.container_name,by_name=True)
            if data is not None:row=row.changed(container_id=data['Id'])
        if row.container_id is not None:
            await asyncio.to_thread(engine.action,row,'destroy')
            report['owned_resources_removed']=True

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--context',required=True);p.add_argument('--image',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();out=Path(a.output);report={'status':'running','provider_requests':0,'scope':'local_linux_arm64_only'}
    assert not out.exists()
    try:
        with tempfile.TemporaryDirectory(dir=out.parent) as directory:asyncio.run(scenario(Path(directory),a.context,a.image,report))
        report['status']='passed'
    except Exception as e:
        report['status']='failed';report['error_type']=type(e).__name__;raise
    finally:out.write_text(json.dumps(report,indent=2)+'\n')
