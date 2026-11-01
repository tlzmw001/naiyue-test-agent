"""One bounded SSH diagnostic keeps identity checks before log access."""
import json
import shlex
import subprocess
import sys

import pytest

from test_docker_environment import record
from test_docker_ssh_transport import intercept
from naiyue_test_agent.execution.docker_cli import DockerCLI
from naiyue_test_agent.execution.docker_environment import DockerEnvironment,labels
from naiyue_test_agent.sandbox_settings import SandboxSettings
from naiyue_test_agent.contracts.sandbox import SandboxError


@pytest.mark.parametrize('case',['valid','foreign','daemon','running','oversize','invalid','restart'])
def test_remote_startup_diagnostic_is_one_ssh_and_keeps_strict_identity(tmp_path,monkeypatch,caplog,case):
    row=record().changed(settings=SandboxSettings(ssh={'host':'target'}).model_dump(mode='json'))
    data=dict(Id=row.container_id,Image=row.image_id,Name='/'+row.container_name,Config={'Labels':labels(row)},
        State=dict(Running=case=='running',Status='exited',ExitCode=1,StartedAt='2026-10-30T01:02:03Z'))
    if case=='foreign':data['Config']['Labels']={}
    observed='2026-10-30T02:00:00Z' if case=='restart' else '' if case=='running' else data['State']['StartedAt']
    raw=json.dumps(dict(daemon={'OSType':'linux','ID':'foreign' if case=='daemon' else row.daemon_id},
        containers=[data])).encode()+b'\n'+observed.encode()+b'\nNYT_ENVD_STARTUP {"code":"env_policy_mismatch"}\n'
    if case=='oversize':raw+=b'PRIVATE'*12000
    if case=='invalid':raw=b'PRIVATE malformed response'
    calls,_=intercept(monkeypatch,raw)
    engine=DockerEnvironment(DockerCLI(SandboxSettings.model_validate(row.settings),tmp_path))
    primary=SandboxError('env_start_failed')
    def fail(*args):raise primary
    engine.request=fail
    with pytest.raises(SandboxError) as caught:engine.handshake(row)
    if case=='valid':assert caught.value.code=='env_policy_mismatch' and caught.value.__cause__ is primary
    else:assert caught.value is primary
    assert len(calls)==1
    assert 'PRIVATE' not in caplog.text
    script=shlex.split(calls[0].argv[-1])[3]
    assert 'container inspect' in script and 'logs' in script
    assert '--since' in script and '--tail' in script


@pytest.mark.parametrize('foreign',[False,True])
def test_real_startup_script_rejects_foreign_identity_before_reading_logs(tmp_path,monkeypatch,foreign):
    from naiyue_test_agent.execution.docker_management import startup_snapshot
    row=record();marker=tmp_path/'logs-read';docker=tmp_path/'docker'
    data=dict(Id=row.container_id,Image=row.image_id,Name='/'+row.container_name,Config={'Labels':labels(row)},
        State=dict(Running=False,Status='exited',ExitCode=1,StartedAt='2026-10-30T01:02:03Z'))
    identity=' '.join([row.container_id,row.image_id,'/'+row.container_name,
        json.dumps(labels(row),sort_keys=True,separators=(',',':'))])
    code=(f'#!{sys.executable}\nimport sys,json\na=sys.argv[1:]\n'
        f'data={data!r}\nidentity={identity!r}\n'
        'if a[2]=="info":\n'
        f' print(json.dumps({{"ID":{row.daemon_id!r},"OSType":"linux"}}) if a[-1]=="{{{{json .}}}}" else {row.daemon_id!r}+" linux")\n'
        'elif a[2:4]==["container","inspect"]:\n'
        ' if "--format" not in a:print(json.dumps([data]))\n'
        ' elif "StartedAt" in a[-2]:print(data["State"]["StartedAt"])\n'
        f' else:print("foreign" if {foreign!r} else identity)\n'
        'elif a[2]=="logs":\n'
        f' open({str(marker)!r},"w").write("read")\n'
        ' print(\'NYT_ENVD_STARTUP {"code":"env_policy_mismatch"}\')\n')
    docker.write_text(code);docker.chmod(0o700)
    cli=DockerCLI(SandboxSettings(ssh={'host':'target','docker_executable':str(docker)}),tmp_path)
    def run(script,**kwargs):
        assert kwargs.get('mutates',False) is False
        completed=subprocess.run(['sh','-c',script],capture_output=True,timeout=5)
        completed.check_returncode();return completed.stdout
    monkeypatch.setattr(cli,'run_script',run)
    if foreign:
        with pytest.raises(subprocess.CalledProcessError):startup_snapshot(cli,row)
        assert not marker.exists()
    else:
        snapshot,logs=startup_snapshot(cli,row)
        assert snapshot['containers']==[data]
        assert logs==b'NYT_ENVD_STARTUP {"code":"env_policy_mismatch"}\n' and marker.exists()
