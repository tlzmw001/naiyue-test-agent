"""Only a verified stopped container's bounded startup event may refine an error."""
import json
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.execution.docker_environment import DockerEnvironment, labels
from test_docker_environment import record


def test_envd_startup_failure_emits_safe_event_and_keeps_original(monkeypatch, caplog):
    from naiyue_test_agent.envd import __main__ as entry
    row = record(); original = SandboxError('env_policy_mismatch')
    monkeypatch.setattr(entry.sys, 'argv', ['envd', 'serve'])
    monkeypatch.setenv('NYT_ENVIRONMENT', json.dumps(row.environment.public()))
    monkeypatch.setenv('NYT_SESSION_ID', row.session_id)
    monkeypatch.setenv('NYT_SANDBOX_POLICY', json.dumps(row.settings))
    def fail(*args, **kwargs): raise original
    monkeypatch.setattr(entry, 'serve', fail)
    with pytest.raises(SandboxError) as caught: entry.main()
    assert caught.value is original
    assert 'NYT_ENVD_STARTUP' in caplog.text and 'env_policy_mismatch' in caplog.text
    assert 'NYT_ENVIRONMENT' not in caplog.text


@pytest.mark.parametrize(('running','foreign','logs','expected'), [
    (False,False,b'PRIVATE\nNYT_ENVD_STARTUP {"code":"env_policy_mismatch"}\n','env_policy_mismatch'),
    (True,False,b'NYT_ENVD_STARTUP {"code":"env_policy_mismatch"}\n','env_stopped'),
    (False,True,b'NYT_ENVD_STARTUP {"code":"env_policy_mismatch"}\n','env_stopped'),
    (False,False,b'NYT_ENVD_STARTUP {"code":"PRIVATE"}\n','env_stopped'),
    (False,False,b'NYT_ENVD_STARTUP invalid PRIVATE\n','env_stopped'),
    (False,False,b'x'*65537,'env_stopped'),
    (False,False,b'NYT_ENVD_STARTUP {"code":"env_policy_mismatch"}\nNYT_ENVD_STARTUP {"code":"env_operation_unknown"}\n','env_stopped'),
])
def test_handshake_refines_only_a_bounded_owned_startup_event(monkeypatch, caplog, running, foreign, logs, expected):
    from naiyue_test_agent.execution.docker_cli import DockerCLI
    row=record(); original=SandboxError('env_stopped'); calls=[]
    data=dict(Id=row.container_id,Image=row.image_id,Name='/'+row.container_name,
        Config={'Labels':labels(row)},State={'Running':running,'Status':'running' if running else 'exited',
            'ExitCode':1,'StartedAt':'2026-10-30T01:02:03Z'})
    if foreign:data['Config']['Labels']={}
    def run(self,args,**kwargs):calls.append(args);return logs
    monkeypatch.setattr(DockerCLI,'daemon',lambda self,expected:row.daemon_id)
    monkeypatch.setattr(DockerCLI,'json',lambda self,args:[data])
    def logs_since(self,identity,*,since):
        assert since==data['State']['StartedAt']
        return run(self,['logs',identity])
    monkeypatch.setattr(DockerCLI,'container_logs',logs_since)
    engine=DockerEnvironment(DockerCLI(SimpleNamespace(),'.'))
    engine.cli.settings=__import__('naiyue_test_agent.sandbox_settings',fromlist=['SandboxSettings']).SandboxSettings.model_validate(row.settings)
    def fail(*args):raise original
    engine.request=fail
    with pytest.raises(SandboxError) as caught:engine.handshake(row)
    assert caught.value.code==expected
    if expected=='env_stopped':assert caught.value is original
    else:assert caught.value.__cause__ is original
    if running or foreign:assert not calls
    assert 'PRIVATE' not in caplog.text


def test_secondary_diagnostic_failure_does_not_replace_handshake_error(monkeypatch, caplog):
    from naiyue_test_agent.execution.docker_cli import DockerCLI
    from naiyue_test_agent.sandbox_settings import SandboxSettings
    original=SandboxError('env_stopped')
    engine=DockerEnvironment(DockerCLI(SandboxSettings(),'.'))
    def fail(*args,**kwargs):raise original
    def unavailable(*args,**kwargs):raise OSError('PRIVATE')
    engine.request=fail;monkeypatch.setattr(DockerCLI,'daemon',unavailable)
    with pytest.raises(SandboxError) as caught:engine.handshake(record())
    assert caught.value is original
    assert 'sandbox_startup_diagnostic_failed' in caplog.text and 'PRIVATE' not in caplog.text


def test_native_log_read_includes_container_stderr_without_exposing_it(tmp_path, monkeypatch, caplog):
    import sys
    from naiyue_test_agent.execution.docker_cli import DockerCLI
    from naiyue_test_agent.sandbox_settings import SandboxSettings
    cli=DockerCLI(SandboxSettings(),tmp_path)
    monkeypatch.setattr(cli,'command',lambda args:[sys.executable,'-c',
        'import sys;sys.stderr.write(\'NYT_ENVD_STARTUP {"code":"env_policy_mismatch"}\\n\')'])
    raw=cli.container_logs('c'*64,since='2026-10-30T01:02:03Z')
    assert raw==b'NYT_ENVD_STARTUP {"code":"env_policy_mismatch"}\n'
    assert 'NYT_ENVD_STARTUP' not in caplog.text
