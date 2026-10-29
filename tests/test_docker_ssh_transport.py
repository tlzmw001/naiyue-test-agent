"""Controlled SSH execution keeps native byte IO, identity and unknown outcomes."""
import json
import os
import shlex
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.execution import docker_cli
from naiyue_test_agent.sandbox_settings import SandboxSettings


def remote(tmp_path, **values):
    ssh = dict(host='company-测试', docker_executable='/opt/Docker CLI/docker', **values)
    return docker_cli.DockerCLI(SandboxSettings.model_validate({'ssh': ssh}), tmp_path)


def intercept(monkeypatch, output=b'answer', returncode=0, termination='exited'):
    calls = []
    lookups = []
    def which(name):
        lookups.append(name)
        return '/client/ssh' if name == 'ssh' else None
    async def run(self, request, consume, control):
        calls.append(request)
        await consume('stdout', output)
        return SimpleNamespace(returncode=returncode, termination=termination)
    monkeypatch.setattr(docker_cli.shutil, 'which', which)
    monkeypatch.setattr(docker_cli.HostProcessRunner, 'run', run)
    return calls, lookups


def test_ssh_never_looks_for_local_docker_and_preserves_remote_argv(tmp_path, monkeypatch):
    calls, lookups = intercept(monkeypatch)
    arguments = ['container', 'exec', '-i', 'owned', 'python', '-c',
                 'print("space 中文")', "x'; $(bad) `bad`\nother", '']
    assert remote(tmp_path).run(arguments) == b'answer'
    assert lookups == ['ssh']
    request = calls[0]
    assert request.argv[:-1] == ['/client/ssh', '-T', '-o', 'BatchMode=yes', '-o',
        'StrictHostKeyChecking=yes', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
        '-o', 'ConnectTimeout=15', '--', 'company-测试']
    assert shlex.split(request.argv[-1]) == [
        'exec', '/opt/Docker CLI/docker', '--host', 'unix:///var/run/docker.sock', *arguments]
    assert request.cwd == tmp_path and request.timeout_seconds == 60


def test_ssh_uses_real_native_binary_stdin_stdout_without_a_text_pipeline(tmp_path, monkeypatch):
    original = docker_cli.HostProcessRunner.run
    calls = []
    monkeypatch.setattr(docker_cli.shutil, 'which', lambda name: sys.executable if name == 'ssh' else None)
    async def echo(self, request, consume, control):
        calls.append(request)
        native = replace(request, argv=[sys.executable, '-c',
            'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'])
        return await original(self, native, consume, control)
    monkeypatch.setattr(docker_cli.HostProcessRunner, 'run', echo)
    payload = bytes(range(256))*1024 + '中文\r\n'.encode()
    assert remote(tmp_path).run(['container', 'exec', '-i', 'owned'], input_bytes=payload) == payload
    assert len(calls) == 1 and calls[0].stdin_fd is not None
    with pytest.raises(OSError):
        os.fstat(calls[0].stdin_fd)


@pytest.mark.parametrize('ssh', [False, True])
def test_native_client_inherits_programdata_without_unrelated_environment(tmp_path, monkeypatch, ssh):
    directory = str(tmp_path/'system-data')
    monkeypatch.setenv('PROGRAMDATA', directory)
    monkeypatch.setenv('NYT_PRIVATE_ENV_FIXTURE', 'must-not-reach-child')
    calls, _ = intercept(monkeypatch)
    if not ssh:
        monkeypatch.setattr(docker_cli.shutil, 'which', lambda _: '/client/docker')
    client = remote(tmp_path) if ssh else docker_cli.DockerCLI(SandboxSettings(), tmp_path)
    client.run(['info'])
    assert calls[0].env['PROGRAMDATA'] == directory
    assert 'NYT_PRIVATE_ENV_FIXTURE' not in calls[0].env


def test_ssh_child_requiring_programdata_does_not_silently_exit_255(tmp_path, monkeypatch):
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path/'system-data'))
    monkeypatch.setattr(docker_cli.shutil, 'which', lambda _: sys.executable)
    original = docker_cli.HostProcessRunner.run
    async def native_client(self, request, consume, control):
        native = replace(request, argv=[sys.executable, '-c',
            'import os,sys; '
            'sys.exit(255) if not os.environ.get("PROGRAMDATA") else None; '
            'sys.stdout.buffer.write(b"programdata-ok")'])
        return await original(self, native, consume, control)
    monkeypatch.setattr(docker_cli.HostProcessRunner, 'run', native_client)
    assert remote(tmp_path).run(['info']) == b'programdata-ok'


def test_remote_daemon_does_not_inspect_any_local_context(tmp_path, monkeypatch):
    calls, _ = intercept(monkeypatch, json.dumps(dict(OSType='linux', ID='original')).encode())
    assert remote(tmp_path).daemon('original') == 'original'
    assert len(calls) == 1
    assert shlex.split(calls[0].argv[-1])[-3:] == ['info', '--format', '{{json .}}']
    with pytest.raises(SandboxError, match='env_binding_mismatch'):
        remote(tmp_path).daemon('different')


@pytest.mark.parametrize('facts,code', [
    (dict(OSType='windows', ID='D'), 'env_platform_unsupported'),
    (dict(OSType='linux', ID=''), 'env_protocol_mismatch'),
])
def test_remote_daemon_requires_a_linux_identity(tmp_path, monkeypatch, facts, code):
    intercept(monkeypatch, json.dumps(facts).encode())
    with pytest.raises(SandboxError, match=code):
        remote(tmp_path).daemon()


def test_missing_ssh_fails_before_creating_a_process_without_local_fallback(tmp_path, monkeypatch):
    calls, _ = intercept(monkeypatch)
    monkeypatch.setattr(docker_cli.shutil, 'which', lambda _: None)
    with pytest.raises(SandboxError, match='env_ssh_unavailable'):
        remote(tmp_path).run(['container', 'create'], mutates=True)
    assert calls == []


@pytest.mark.parametrize('mutates,code', [(False, 'env_unreachable'), (True, 'env_operation_unknown')])
@pytest.mark.parametrize('returncode,termination', [(255, 'exited'), (None, 'timeout')])
def test_disconnect_and_timeout_never_replay_a_remote_command(tmp_path, monkeypatch, caplog,
                                                            mutates, code, returncode, termination):
    calls, _ = intercept(monkeypatch, b'', returncode, termination)
    with pytest.raises(SandboxError, match=code):
        remote(tmp_path).run(['container', 'create'], mutates=mutates)
    assert len(calls) == 1
    assert 'docker_request_failed' in caplog.text and 'company-测试' not in caplog.text


def test_original_local_transport_still_uses_its_context(tmp_path, monkeypatch):
    calls, _ = intercept(monkeypatch)
    monkeypatch.setattr(docker_cli.shutil, 'which', lambda _: '/local/docker')
    local = docker_cli.DockerCLI(SandboxSettings(context='owned-local'), tmp_path)
    assert local.run(['info']) == b'answer'
    assert calls[0].argv == ['/local/docker', '--context', 'owned-local', 'info']


@pytest.mark.parametrize('mutates,code', [(False, 'env_message_too_large'), (True, 'env_operation_unknown')])
def test_ssh_output_budget_preserves_unknown_write_outcome(tmp_path, monkeypatch, mutates, code):
    calls, _ = intercept(monkeypatch, b'x'*65537)
    cli = docker_cli.DockerCLI(SandboxSettings.model_validate(dict(
        ssh=dict(host='company'), max_message_bytes=65536)), tmp_path)
    with pytest.raises(SandboxError, match=code):
        cli.run(['container', 'exec'], mutates=mutates)
    assert len(calls) == 1


def test_ssh_transport_failure_does_not_log_private_stderr_or_exception_text(tmp_path, monkeypatch, caplog):
    intercept(monkeypatch)
    async def fail(self, request, consume, control):
        await consume('stderr', b'private-host private-credential')
        raise OSError('private-host private-credential')
    monkeypatch.setattr(docker_cli.HostProcessRunner, 'run', fail)
    with pytest.raises(SandboxError, match='env_unreachable') as caught:
        remote(tmp_path).run(['info'])
    assert str(caught.value) == 'env_unreachable'
    assert 'private-host' not in caplog.text and 'private-credential' not in caplog.text


@pytest.mark.parametrize('endpoint', ['unix:///var/run/docker.sock', 'npipe:////./pipe/docker_engine', 'ssh://other'])
def test_local_endpoint_contract_is_not_relaxed_by_adding_ssh(tmp_path, monkeypatch, endpoint):
    cli = docker_cli.DockerCLI(SandboxSettings(), tmp_path)
    calls = []
    def json_result(arguments):
        calls.append(arguments)
        return ([{'Endpoints': {'docker': {'Host': endpoint}}}] if arguments[0] == 'context'
                else {'OSType': 'linux', 'ID': 'original'})
    monkeypatch.setattr(cli, 'json', json_result)
    if endpoint.startswith('ssh:'):
        with pytest.raises(SandboxError, match='env_endpoint_unsupported'):
            cli.daemon()
        assert len(calls) == 1
    else:
        assert cli.daemon('original') == 'original'
        assert calls[0] == ['context', 'inspect', 'default']


def test_remote_connection_facts_are_not_sent_to_the_container_policy(tmp_path, monkeypatch):
    from test_docker_environment import record
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment, labels
    settings = SandboxSettings.model_validate(dict(image='app:test', ssh=dict(host='private-control-host')))
    row = record().changed(settings=settings.model_dump(mode='json'))
    from naiyue_test_agent.execution.docker_environment import create_arguments
    command=create_arguments(row)
    policy=json.loads(next(item for item in command if item.startswith('NYT_SANDBOX_POLICY=')).split('=',1)[1])
    assert policy==SandboxSettings(image='app:test').model_dump(mode='json')
    assert 'private-control-host' not in json.dumps(command)
    # The batch creation lifecycle and identity save are covered by the real
    # current entry in test_sandbox_management_batch, rather than the old CLI stub.
    assert row.settings['ssh']['host']=='private-control-host'
    assert command[:2]==['container','create']
