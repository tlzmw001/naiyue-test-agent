"""Remote management batches preserve quoting and fail before dependent mutations."""
import json
import shlex
import subprocess
from pathlib import Path

import pytest

from test_docker_environment import record
from naiyue_test_agent.execution.docker_cli import DockerCLI
from naiyue_test_agent.sandbox_settings import SandboxSettings
from naiyue_test_agent.contracts.sandbox import SandboxError


def test_validation_uses_one_ssh_and_verifies_full_facts(tmp_path,monkeypatch):
    from naiyue_test_agent.execution.docker_management import validation_snapshot
    from test_docker_ssh_transport import intercept
    row=record();value={'daemon':{'OSType':'linux','ID':'D'},'containers':[{'Id':row.container_id}],
        'volumes':[{'Name':row.volume_name},{'Name':'nyt-s-'+row.binding_id}]}
    calls,_=intercept(monkeypatch,json.dumps(value).encode())
    cli=DockerCLI(SandboxSettings(ssh={'host':'target'}),tmp_path)
    assert validation_snapshot(cli,row)==value
    assert len(calls)==1
    argv=shlex.split(calls[0].argv[-1]);assert argv[:3]==['exec','sh','-c']
    assert 'container inspect' in argv[3] and 'volume inspect' in argv[3]


def test_remote_batch_does_not_interpolate_shell_metacharacters(tmp_path,monkeypatch):
    from naiyue_test_agent.execution.docker_management import json_batch
    from test_docker_ssh_transport import intercept
    calls,_=intercept(monkeypatch,b'{"item":[]}')
    cli=DockerCLI(SandboxSettings(ssh={'host':'target'}),tmp_path)
    evil="x'; $(touch injected) `touch injected`\nother"
    assert json_batch(cli,[('item',['image','inspect',evil])])=={'item':[]}
    script=shlex.split(calls[0].argv[-1])[3]
    executable=tmp_path/'docker';executable.write_text('#!/bin/sh\npython3 -c \'import json,sys;print(json.dumps(sys.argv[1:]))\' "$@"\n');executable.chmod(0o700)
    # Execute the same generated script with a deterministic local Docker substitute.
    script=script.replace('docker --host',shlex.quote(str(executable))+' --host')
    result=subprocess.run(['sh','-c',script],cwd=tmp_path,capture_output=True,check=True)
    assert json.loads(result.stdout)['item'][-1]==evil
    assert not (tmp_path/'injected').exists()


def test_remote_batch_failure_is_finite_and_mutation_is_not_replayed(tmp_path,monkeypatch):
    from naiyue_test_agent.execution.docker_management import json_batch
    from test_docker_ssh_transport import intercept
    calls,_=intercept(monkeypatch,b'',returncode=255)
    cli=DockerCLI(SandboxSettings(ssh={'host':'target'}),tmp_path)
    with pytest.raises(SandboxError,match='env_operation_unknown'):
        json_batch(cli,[('item',['volume','create','owned'])],mutates=True)
    assert len(calls)==1


def test_remote_environment_validation_consumes_one_complete_snapshot(tmp_path,monkeypatch):
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    from naiyue_test_agent.execution import docker_management
    row=record().changed(settings=SandboxSettings(ssh={'host':'target'}).model_dump(mode='json'))
    calls=[];snapshot={'snapshot':'checked'}
    monkeypatch.setattr(docker_management,'validation_snapshot',lambda cli,row:(calls.append(row) or snapshot))
    monkeypatch.setattr(docker_management,'validate_snapshot',lambda original,value,**kw: (original,value,kw))
    cli=DockerCLI(SandboxSettings.model_validate(row.settings),tmp_path)
    assert DockerEnvironment(cli).validate(row,running=False)==(row,snapshot,{'running':False})
    assert calls==[row]


def test_remote_creation_batches_metadata_before_start_and_keeps_unknown_identity(tmp_path,monkeypatch):
    from naiyue_test_agent.execution.docker_management import create_remote
    from naiyue_test_agent.execution import docker_environment
    row=record();created=row.changed(container_id='d'*64);events=[]
    monkeypatch.setattr(docker_environment,'verify_container',lambda value,data:events.append(('verify',value.container_id)))
    volumes=[dict(Name=name,Labels=docker_environment.labels(row)) for name in (row.volume_name,'nyt-s-'+row.binding_id)]
    class CLI:
        settings=SandboxSettings(ssh={'host':'target'})
        def run_script(self,script,**kwargs):
            events.append(('batch',kwargs['mutates']))
            assert script.index('volume create')<script.index('container create')
            assert 'test ' in script
            return json.dumps(dict(daemon=dict(OSType='linux',ID=row.daemon_id),containers=[{'Id':created.container_id}],volumes=volumes)).encode()
        def run(self,args,**kwargs):events.append(('start',args[-1]));return b''
    def save(value):events.append(('save',value.container_id))
    assert create_remote(CLI(),row,['container','create','image'],save)==created
    assert events==[('batch',True),('save',created.container_id),('verify',created.container_id),('start',created.container_id)]


def test_attach_requires_controller_ack_before_entering_container(tmp_path):
    from naiyue_test_agent.execution.docker_management import attach_command
    row=record();executable=tmp_path/'docker';marker=tmp_path/'executed'
    executable.write_text('#!/bin/sh\ncase "$3 $4" in\n "container exec") touch '+shlex.quote(str(marker))+';;\n *) printf "%s\\n" "{}";;\nesac\n')
    executable.chmod(0o700)
    cli=DockerCLI(SandboxSettings(ssh={'host':'target','docker_executable':str(executable)}),tmp_path)
    # Use the generated remote script directly: stdin closes without approval.
    import unittest.mock
    with unittest.mock.patch('naiyue_test_agent.execution.docker_cli.shutil.which',return_value='/client/ssh'):
        script=shlex.split(attach_command(cli,row,['container','exec',row.container_id,'python'])[-1])[3]
    result=subprocess.run(['sh','-c',script],input=b'',capture_output=True,timeout=5)
    assert result.returncode!=0 and not marker.exists()
    result=subprocess.run(['sh','-c',script],input=b'NYT-ATTACH-OK\n',capture_output=True,timeout=5)
    assert result.returncode==0 and marker.exists()


def test_failed_docker_info_cannot_become_a_successful_test(tmp_path):
    from naiyue_test_agent.execution.docker_management import create_remote
    marker=tmp_path/'created';executable=tmp_path/'docker'
    executable.write_text('#!/bin/sh\ncase "$3 $4" in\n "info --format") printf "D\\n";exit 7;;\n "container create") touch '+shlex.quote(str(marker))+';;\nesac\n')
    executable.chmod(0o700)
    class CLI:
        settings=SandboxSettings(ssh={'host':'target','docker_executable':str(executable)})
        def run_script(self,script,**kwargs):
            result=subprocess.run(['sh','-c',script],capture_output=True,timeout=5)
            assert result.returncode==7
            raise SandboxError('env_operation_unknown')
    with pytest.raises(SandboxError,match='env_operation_unknown'):
        create_remote(CLI(),record(),['container','create','fixture'],lambda row:None)
    assert not marker.exists()
