"""Only an explicit first-create permit may start a saved container over attach."""
import asyncio
import json
import shlex
import subprocess
import sys

import pytest

from test_docker_environment import record
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.execution.docker_cli import DockerCLI
from naiyue_test_agent.sandbox_settings import SandboxSettings


@pytest.mark.parametrize('ack',[b'',b'NYT-ATTACH-OK\n'])
def test_start_is_after_ack_and_before_attach_without_polluting_stdout(tmp_path,monkeypatch,ack):
    from naiyue_test_agent.execution.docker_management import attach_command
    events=tmp_path/'events';docker=tmp_path/'docker'
    docker.write_text('#!/bin/sh\ncase "$3 $4" in\n'
        ' "container start") printf "start\\n" >> '+shlex.quote(str(events))+'; printf "container-id\\n";;\n'
        ' "container exec") printf "exec\\n" >> '+shlex.quote(str(events))+';;\n'
        ' *) printf "{}\\n";;\nesac\n')
    docker.chmod(0o700)
    row=record().changed(lifecycle='creating')
    cli=DockerCLI(SandboxSettings(ssh={'host':'target','docker_executable':str(docker)}),tmp_path)
    monkeypatch.setattr('naiyue_test_agent.execution.docker_cli.shutil.which',lambda _: '/client/ssh')
    script=shlex.split(attach_command(cli,row,['container','exec',row.container_id,'python'],start=True)[-1])[3]
    result=subprocess.run(['sh','-c',script],input=ack,capture_output=True,timeout=5)
    assert json.loads(result.stdout)=={'daemon':{},'containers':{},'volumes':{}}
    if ack:
        assert result.returncode==0 and events.read_text()=='start\nexec\n'
    else:assert result.returncode!=0 and not events.exists()


def test_start_failure_never_executes_attach(tmp_path,monkeypatch):
    from naiyue_test_agent.execution.docker_management import attach_command
    marker=tmp_path/'executed';docker=tmp_path/'docker'
    docker.write_text('#!/bin/sh\ncase "$3 $4" in\n "container start") exit 17;;\n'
        ' "container exec") touch '+shlex.quote(str(marker))+';;\n *) printf "{}\\n";;\nesac\n')
    docker.chmod(0o700)
    cli=DockerCLI(SandboxSettings(ssh={'host':'target','docker_executable':str(docker)}),tmp_path)
    monkeypatch.setattr('naiyue_test_agent.execution.docker_cli.shutil.which',lambda _: '/client/ssh')
    row=record().changed(lifecycle='creating')
    script=shlex.split(attach_command(cli,row,['container','exec',row.container_id,'python'],start=True)[-1])[3]
    result=subprocess.run(['sh','-c',script],input=b'NYT-ATTACH-OK\n',capture_output=True,timeout=5)
    assert result.returncode==3 and not marker.exists()


@pytest.mark.parametrize('expected',[False,True])
def test_only_permitted_start_failure_exit_is_classified_as_start_failure(expected):
    from naiyue_test_agent.execution.sandbox_channel import SandboxChannel
    row=record()
    async def scenario():
        code='import sys;print("{}",flush=True);sys.stdin.buffer.readline();sys.exit(3)'
        with pytest.raises(SandboxError) as caught:
            await SandboxChannel.open([sys.executable,'-c',code],environment=row.environment.public(),
                session_id=row.session_id,preamble_check=lambda _:None,start_expected=expected)
        assert caught.value.code==('env_start_failed' if expected else 'env_protocol_mismatch')
    asyncio.run(scenario())


def test_first_attempt_consumes_start_permit_and_reconnect_is_readonly(tmp_path,monkeypatch):
    from naiyue_test_agent.execution.sandbox_transport import SandboxTransport
    from naiyue_test_agent.sandbox_profiles import SandboxConfiguration
    flags=[];checks=[];attempts=[]
    def attach(cli,row,args,*,start=False):flags.append(start);return ['unused']
    def validate(row,value,*,running=True):checks.append(running)
    class Channel:
        failed=None
        async def request(self,*args,**kwargs):return {}
        async def close(self,**kwargs):pass
    async def open(*args,**kwargs):
        kwargs['preamble_check']({});attempts.append(1)
        if len(attempts)==1:raise SandboxError('env_unreachable')
        return Channel()
    monkeypatch.setattr('naiyue_test_agent.execution.docker_management.attach_command',attach)
    monkeypatch.setattr('naiyue_test_agent.execution.docker_management.validate_snapshot',validate)
    monkeypatch.setattr('naiyue_test_agent.execution.sandbox_channel.SandboxChannel.open',open)
    transport=SandboxTransport(tmp_path,SandboxConfiguration())
    row=record().changed(lifecycle='creating',settings=SandboxSettings(ssh={'host':'target'}).model_dump(mode='json'))
    try:
        with pytest.raises(SandboxError):transport.start_created(row)
        assert transport.request(row,'hello',{})=={}
        assert flags==[True,False] and checks==[False,True]
        assert not transport._starting
    finally:asyncio.run(transport.aclose())


def test_ordinary_creating_connection_and_recover_cannot_grant_start(tmp_path,monkeypatch):
    from naiyue_test_agent.execution.sandbox_transport import SandboxTransport
    from naiyue_test_agent.sandbox_profiles import SandboxConfiguration
    flags=[]
    def attach(cli,row,args,*,start=False):flags.append(start);return ['unused']
    class Channel:
        failed=None
        async def request(self,*args,**kwargs):return {}
        async def close(self,**kwargs):pass
    async def open(*args,**kwargs):
        kwargs['preamble_check']({});return Channel()
    monkeypatch.setattr('naiyue_test_agent.execution.docker_management.attach_command',attach)
    monkeypatch.setattr('naiyue_test_agent.execution.docker_management.validate_snapshot',lambda *a,**k:None)
    monkeypatch.setattr('naiyue_test_agent.execution.sandbox_channel.SandboxChannel.open',open)
    transport=SandboxTransport(tmp_path,SandboxConfiguration())
    row=record().changed(lifecycle='creating',settings=SandboxSettings(ssh={'host':'target'}).model_dump(mode='json'))
    try:
        transport.request(row,'hello',{})
        assert flags==[False]
        with pytest.raises(SandboxError):transport.start_created(row.changed(lifecycle='running'))
    finally:asyncio.run(transport.aclose())


def test_first_start_and_hello_share_the_measured_handshake_boundary():
    from types import SimpleNamespace
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    from naiyue_test_agent.contracts.sandbox import PROTOCOL_VERSION
    row=record().changed(lifecycle='creating');events=[]
    engine=DockerEnvironment(SimpleNamespace())
    class Transport:
        def start_created(self,value):events.append(('start',value.container_id))
        def request(self,value,operation,params,**kwargs):
            events.append((operation,value.container_id))
            return dict(protocol=PROTOCOL_VERSION,package_version='0.5.6',
                environment=value.environment.public(),session_id=value.session_id)
    engine.transport=Transport()
    engine.handshake(row,start=True)
    assert events==[('start',row.container_id),('hello',row.container_id)]
    events.clear();engine.handshake(row)
    assert events==[('hello',row.container_id)]


@pytest.mark.parametrize('existing',['none','work','state','foreign'])
def test_remote_creation_reports_freshness_before_touching_exact_named_volumes(tmp_path,monkeypatch,existing):
    from naiyue_test_agent.execution.docker_management import create_remote
    from naiyue_test_agent.execution import docker_environment
    row=record();identity='d'*64;docker=tmp_path/'docker'
    prior={'none':[],'work':[row.volume_name],'state':['nyt-s-'+row.binding_id],'foreign':['other-volume']}[existing]
    labels=docker_environment.labels(row)
    docker.write_text('#!'+sys.executable+'\nimport json,re,sys\na=sys.argv[3:]\n'
        'labels='+repr(labels)+'\nprior='+repr(prior)+'\n'
        'if a[0]=="info":\n print("D" if a[-1]=="{{.ID}}" else json.dumps(dict(ID="D",OSType="linux")))\n'
        'elif a[:2]==["volume","ls"]:\n pattern=a[a.index("--filter")+1].removeprefix("name=")\n print("\\n".join(x for x in prior if re.search(pattern,x)))\n'
        'elif a[:2]==["volume","create"]: print(a[-1])\n'
        'elif a[:2]==["volume","inspect"]:\n print(json.dumps(labels,sort_keys=True,separators=(",",":")) if "--format" in a else json.dumps([dict(Name=x,Labels=labels) for x in a[2:]]))\n'
        'elif a[:2]==["container","create"]: print('+repr(identity)+')\n'
        'elif a[:2]==["container","inspect"]: print(json.dumps([dict(Id='+repr(identity)+')]))\n'
        'else: raise SystemExit(19)\n')
    docker.chmod(0o700)
    monkeypatch.setattr(docker_environment,'verify_container',lambda *args:None)
    class CLI:
        settings=SandboxSettings(ssh={'host':'target','docker_executable':str(docker)})
        def run_script(self,script,**kwargs):
            assert script.index('volume ls')<script.index('volume create')
            return subprocess.run(['sh','-c',script],capture_output=True,timeout=10,check=True).stdout
    created,fresh=create_remote(CLI(),row,['container','create','fixture'],lambda value:None)
    assert created.container_id==identity and fresh is (existing in ('none','foreign'))
