import json,shlex
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError


def test_ssh_uses_fixed_isolated_command_quotes_paths_and_bounds_output(monkeypatch):
    from naiyue_test_agent.cloud_install.remote import execute_remote,RemoteRequest
    from naiyue_test_agent.cloud_install.settings import RemoteUpdateSettings
    import naiyue_test_agent.cloud_install.remote as remote
    config=RemoteUpdateSettings(ssh_host='approved-dev',remote_python="/opt/agent one's/venv/bin/python",remote_settings='/opt/agent config/update.json')
    request=RemoteRequest(action='update',release_id='r2',command_id=str(uuid4()));seen=[]
    class Runner:
        async def run(self,r,consume,control):
            seen.append(r);await consume('stdout',json.dumps(dict(command_id=request.command_id,phase='queued')).encode())
            return SimpleNamespace(termination='exited',returncode=0,output_complete=True)
    monkeypatch.setattr(remote,'HostProcessRunner',Runner)
    result=execute_remote(config,request)
    assert result['command_id']==request.command_id
    argv=seen[0].argv
    assert argv[0]=='ssh' and argv[-2]=='approved-dev' and '-T' in argv and 'StrictHostKeyChecking=yes' in argv
    words=shlex.split(argv[-1]);assert words[:5]==[config.remote_python,'-I','-m','naiyue_test_agent.cli','cloud']
    assert words[-2:]==['--settings',config.remote_settings]
    assert '--command-id' in words and config.remote_settings not in seen[0].env.values()


@pytest.mark.parametrize('value',['-oProxyCommand=evil','x;whoami','x\nother'])
def test_remote_alias_rejects_shell_or_option_injection(value):
    from naiyue_test_agent.cloud_install.settings import RemoteUpdateSettings
    with pytest.raises(ValidationError):RemoteUpdateSettings(ssh_host=value,remote_python='/opt/python',remote_settings='/opt/update.json')


def test_remote_target_is_not_arbitrary_shell():
    from naiyue_test_agent.cloud_install.remote import RemoteRequest
    with pytest.raises(ValidationError):RemoteRequest(action='update',release_id='r2; touch /tmp/x',command_id=str(uuid4()))
    with pytest.raises(ValidationError):RemoteRequest(action='shell',release_id='r2',command_id=str(uuid4()))
