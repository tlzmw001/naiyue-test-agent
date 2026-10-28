"""Failure facts distinguish infrastructure faults without changing tool feedback."""
import asyncio
import io
import logging
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.execution.docker_cli import DockerCLI
from naiyue_test_agent.execution.git_repository import GitRepository
from naiyue_test_agent.sandbox_settings import SandboxSettings, GitInputSettings
from naiyue_test_agent.tools.processes import HostProcessRunner


@pytest.mark.parametrize(('family', 'stderr', 'expected'), [
    ('docker', b'Permission denied (publickey). PRIVATE_TOKEN', 'authentication_failed'),
    ('docker', b'connect: Connection refused PRIVATE_TOKEN', 'connection_refused'),
    ('git', b'fatal: Could not resolve host PRIVATE_TOKEN', 'name_resolution_failed'),
    ('git', b'fatal: No space left on device PRIVATE_TOKEN', 'disk_full'),
])
def test_native_failure_logs_safe_reason_without_changing_error(tmp_path, monkeypatch, caplog, family, stderr, expected):
    async def run(self, request, consume, control):
        await consume('stderr', stderr)
        return SimpleNamespace(returncode=255 if family == 'docker' else 128, termination='exited')
    monkeypatch.setattr(HostProcessRunner, 'run', run)
    monkeypatch.setattr('shutil.which', lambda _: '/fixture/executable')
    if family == 'docker':
        client = DockerCLI(SandboxSettings(ssh={'host': 'fixture'}), tmp_path)
        with pytest.raises(SandboxError) as caught:
            client.run(['info'])
        assert caught.value.code == 'env_unreachable'
    else:
        client = GitRepository(tmp_path, GitInputSettings())
        with pytest.raises(GitInputError) as caught:
            client.run(['fetch', 'PRIVATE_URL'])
        assert caught.value.code == 'git_input_git_failed'
    assert expected in caplog.text
    assert 'stderr_bytes' in caplog.text and 'command' in caplog.text
    assert 'PRIVATE_TOKEN' not in caplog.text and 'PRIVATE_URL' not in caplog.text


def test_git_timeout_logged_but_expected_nonzero_probe_stays_quiet(tmp_path, monkeypatch, caplog):
    outcome = SimpleNamespace(returncode=None, termination='timeout')
    async def run(*args):
        return outcome
    monkeypatch.setattr(HostProcessRunner, 'run', run)
    monkeypatch.setattr('shutil.which', lambda _: '/fixture/git')
    client = GitRepository(tmp_path, GitInputSettings())
    with pytest.raises(GitInputError, match='git_input_timeout'):
        client.run(['fetch'])
    assert 'timeout' in caplog.text and 'timeout_seconds' in caplog.text
    caplog.clear()
    outcome.returncode, outcome.termination = 1, 'exited'
    assert client.run(['rev-parse', '--verify', 'missing'], check=False) == (1, b'')
    assert not caplog.records


def test_formatter_retains_safe_event_identity_and_omits_bodies():
    from naiyue_test_agent.diagnostics import SafeDiagnosticFormatter
    record = logging.LogRecord('fixture', logging.ERROR, '', 0, 'application_run_event', (), None)
    record.application_event = dict(session_id='session-1', run_id='run-1', stage='finalize',
        error_type='PermissionError', prompt='PRIVATE_PROMPT', output='PRIVATE_OUTPUT')
    rendered = SafeDiagnosticFormatter().format(record)
    assert 'session-1' in rendered and 'run-1' in rendered and 'finalize' in rendered
    assert 'PRIVATE' not in rendered
    assert rendered[:4].isdigit()


def test_exception_group_diagnostics_are_bounded_and_do_not_read_exception_text():
    from naiyue_test_agent.diagnostics import diagnostic_text
    error = ExceptionGroup('PRIVATE_GROUP', [PermissionError(13, 'PRIVATE_PATH')] * 100)
    value = diagnostic_text(error=error, phase='connect', command='PRIVATE\nCOMMAND', password='PRIVATE_PASSWORD')
    assert 'PermissionError' in value and '13' in value and 'leaves_truncated' in value
    assert 'PRIVATE' not in value and len(value) < 4096


def test_taskkill_failure_retains_its_return_code(monkeypatch, caplog):
    from naiyue_test_agent.tools import processes
    transport = SimpleNamespace(get_pid=lambda: 42, get_returncode=lambda: None)
    killer = SimpleNamespace(get_returncode=lambda: 5, close=lambda: None)
    async def spawn(*args, **kwargs):
        return killer, None
    async def probe():
        monkeypatch.setattr(asyncio.get_running_loop(), 'subprocess_exec', spawn)
        monkeypatch.setattr(processes, 'os', SimpleNamespace(name='nt'))
        monkeypatch.setattr(processes.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
        with pytest.raises(OSError, match='Process tree termination failed'):
            await HostProcessRunner._terminate(transport)
    asyncio.run(probe())
    assert 'taskkill_exit_code' in caplog.text and '5' in caplog.text and '42' in caplog.text


def test_command_error_output_still_goes_to_model_feedback():
    from langchain_core.tools import ToolException
    from naiyue_test_agent.tools.process_execution import format_process_outcome
    from naiyue_test_agent.agent_runtime.tool_execution.feedback import tool_error_feedback
    from naiyue_test_agent.tools.processes import ProcessCleanupError
    output = SimpleNamespace(truncated=False, lossy=False, observed_lines=1, observed_bytes=22,
        start_line=1, partial_line=False, reasons=set(), text='assert expected == actual')
    with pytest.raises(ToolException) as caught:
        format_process_outcome(SimpleNamespace(returncode=1, output_complete=True, termination='exited'),
            output, SimpleNamespace(paths={}, reason=None), label='Bash')
    request = SimpleNamespace(tool_call={'name': 'bash', 'id': 'call-1'}, runtime=SimpleNamespace(
        context=SimpleNamespace(scope=SimpleNamespace(identity=SimpleNamespace(session_id='s', run_id='r')))))
    feedback = tool_error_feedback(caught.value, request)
    assert 'assert expected == actual' in feedback and 'exit_code=1' in feedback
    assert tool_error_feedback(ProcessCleanupError(), request) is None


def test_service_spawn_failure_logs_system_code_without_private_path(monkeypatch, caplog):
    from naiyue_test_agent.service import launcher
    from naiyue_test_agent.service.settings import ServiceError
    monkeypatch.setattr(launcher, 'service_running', lambda _: False)
    def spawn(*args):
        raise PermissionError(13, 'PRIVATE_PATH')
    monkeypatch.setattr(launcher, '_spawn', spawn)
    with pytest.raises(ServiceError, match='service_start_failed'):
        asyncio.run(launcher.ensure_service(SimpleNamespace(startup_timeout=1)))
    assert '"errno":13' in caplog.text and 'spawn' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_docker_rpc_failure_logs_request_identity_without_payload(tmp_path, caplog):
    from naiyue_test_agent.contracts.environment import EnvironmentRef
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    original = SandboxError('env_unreachable')
    def run(*args, **kwargs):
        raise original
    engine = DockerEnvironment(SimpleNamespace(run=run))
    engine.validate = lambda _: None
    row = SimpleNamespace(binding_id='binding-1', session_id='session-1', container_id='container-1',
        environment=EnvironmentRef('binding-1','sandbox','project-1','/workspace','linux'),
        settings={'max_message_bytes':65536})
    with pytest.raises(SandboxError) as caught:
        engine.request(row,'operation_status',{'operation_id':'operation-1','private':'PRIVATE_PAYLOAD'})
    assert caught.value is original
    assert 'binding-1' in caplog.text and 'operation_status' in caplog.text and 'operation-1' in caplog.text
    assert 'request_id' in caplog.text and 'PRIVATE' not in caplog.text


def test_diagnostic_handler_failure_does_not_replace_rpc_error(monkeypatch, caplog):
    from naiyue_test_agent.execution import docker_environment
    original = SandboxError('env_unreachable')
    engine = docker_environment.DockerEnvironment(None)
    def fail(*args, **kwargs):
        raise original
    def broken_log(*args, **kwargs):
        raise OSError(28, 'PRIVATE_LOG_PATH')
    engine.validate = fail
    monkeypatch.setattr(docker_environment.logger, 'warning', broken_log)
    row = SimpleNamespace(binding_id='binding-1', session_id='session-1')
    with pytest.raises(SandboxError) as caught:
        engine.request(row, 'hello', {})
    assert caught.value is original
    assert 'diagnostic_log_failed' in caplog.text and 'PRIVATE' not in caplog.text


def test_diagnostic_projection_failure_preserves_original_exception(caplog):
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    class UnreadableGroup(ExceptionGroup):
        @property
        def exceptions(self):
            raise ValueError('PRIVATE_DIAGNOSTIC_PROPERTY')
    original = UnreadableGroup('PRIVATE_GROUP', [ValueError('PRIVATE_MEMBER')])
    engine = DockerEnvironment(None)
    def fail(*args):
        raise original
    engine.validate = fail
    retained, failure_type = False, None
    try:
        engine.request(SimpleNamespace(binding_id='b', session_id='s'), 'hello', {})
    except Exception as caught:
        retained, failure_type = caught is original, type(caught).__name__
    # Do not ask pytest to render the intentionally unreadable exception tree.
    assert retained, failure_type
    assert original.__context__ is None
    assert 'diagnostic_projection_failed' in caplog.text and 'PRIVATE' not in caplog.text


def test_docker_json_failure_logs_decode_phase_without_response(tmp_path, caplog):
    client = DockerCLI(SandboxSettings(), tmp_path)
    client.run = lambda args: b'PRIVATE_INVALID_JSON'
    with pytest.raises(SandboxError, match='env_protocol_mismatch'):
        client.json(['info'])
    assert 'json_decode' in caplog.text and 'JSONDecodeError' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_git_stderr_budget_reports_observed_bytes_even_when_chunk_is_rejected(tmp_path, monkeypatch, caplog):
    async def run(self, request, consume, control):
        await consume('stderr', b'x' * 2048)
    monkeypatch.setattr(HostProcessRunner, 'run', run)
    monkeypatch.setattr('shutil.which', lambda _: '/fixture/git')
    client = GitRepository(tmp_path, GitInputSettings(max_command_bytes=1024))
    with pytest.raises(GitInputError, match='git_input_budget'):
        client.run(['fetch'])
    assert '"stderr_bytes":2048' in caplog.text
    assert '"stderr_present":true' in caplog.text and '"stderr_truncated":true' in caplog.text


def test_controller_accepts_existing_envd_053_protocol_without_image_upgrade():
    import json
    from naiyue_test_agent.contracts.environment import EnvironmentRef
    from naiyue_test_agent.contracts.sandbox import PROTOCOL_VERSION
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    environment = EnvironmentRef('binding-1', 'sandbox', 'project-1', '/workspace', 'linux')
    def run(args, *, input_bytes, **kwargs):
        request = json.loads(input_bytes)
        assert request['operation'] == 'hello'
        return json.dumps(dict(protocol=PROTOCOL_VERSION, request_id=request['request_id'],
            environment=environment.public(), session_id='session-1', ok=True,
            result=dict(protocol=PROTOCOL_VERSION, package_version='0.5.3',
                environment=environment.public(), session_id='session-1'))).encode()
    engine = DockerEnvironment(SimpleNamespace(run=run))
    engine.validate = lambda _: None
    row = SimpleNamespace(binding_id='binding-1', session_id='session-1', environment=environment,
        container_id='original-container', settings={'max_message_bytes':65536})
    engine.handshake(row)
    assert row.container_id == 'original-container'
