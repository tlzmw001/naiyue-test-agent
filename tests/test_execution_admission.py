"""Explicit execution identity is required before any process ownership begins."""

from runtime_context_helpers import host_files
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.application.sessions import background_tasks
from naiyue_test_agent.execution_roots import ExecutionRoot, ExecutionRootUnavailable, validate_execution_root
from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessOutcome, ProcessRequest
from naiyue_test_agent.tools.settings import BashSettings
from naiyue_test_agent.workspaces import prepare_workspace


def deny_execution_root(monkeypatch, root):
    """Inject root-only IO failure while leaving history/archive storage usable."""
    original = Path.stat
    def stat(path, *args, **kwargs):
        if path == root:
            raise PermissionError('synthetic denied root')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)


@pytest.mark.parametrize('entry', ['submit', 'foreground'])
@pytest.mark.parametrize('invalid', ['omitted', 'none', 'wrong_type', 'root_mismatch', 'cwd_mismatch', 'missing', 'unreadable'])
def test_invalid_process_identity_never_registers_or_captures(monkeypatch, tmp_path, entry, invalid):
    captures, calls = [], []
    original_capture = background_tasks.ProcessCapture

    def capture(*args, **kwargs):
        captures.append(True)
        return original_capture(*args, **kwargs)

    class Runner:
        async def run(self, request, consume, control=None):
            calls.append(request)
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)

    monkeypatch.setattr(background_tasks, 'ProcessCapture', capture)

    async def scenario():
        manager = background_tasks.BackgroundProcessManager()
        root = tmp_path / 'execution'
        root.mkdir()
        other = tmp_path / 'other'
        other.mkdir()
        arguments = dict(session_id='S', workspace_root=root, environment=host_files(root).environment, archive_root=tmp_path / 'archive',
            run_id='R', tool_call_id='C', agent_path='root', command='unused', name='bash',
            request=ProcessRequest(['unused'], other if invalid == 'cwd_mismatch' else root, {}, execution_environment=host_files(root).environment.public()),
            settings=BashSettings(), runner=Runner(), control=None)
        if invalid != 'omitted':
            arguments['execution_root'] = {
                'none': None, 'wrong_type': SimpleNamespace(path=root),
                'root_mismatch': ExecutionRoot(other), 'cwd_mismatch': ExecutionRoot(root),
                'missing': ExecutionRoot(root), 'unreadable': ExecutionRoot(root),
            }[invalid]
        if invalid == 'missing':
            root.rmdir()
        elif invalid == 'unreadable':
            deny_execution_root(monkeypatch, root)
        if entry == 'foreground':
            arguments.update(wait_seconds=1, background_limit=1024, on_progress=lambda *_: None)
        try:
            error, match = (TypeError, 'execution_root') if invalid == 'omitted' else (
                ToolException, '^Background task scope is unavailable\\.$')
            if invalid in ('missing', 'unreadable'):
                match = f'^execution_root_unavailable: {invalid}$'
            with pytest.raises(error, match=match):
                await getattr(manager, entry)(**arguments)
            assert (await manager.list())['items'] == []
            assert manager._entries == {} and manager._calls == {}
        finally:
            await manager.aclose()
        assert captures == [] and calls == []
        assert not (tmp_path / 'archive').exists()

    asyncio.run(scenario())


@pytest.mark.parametrize('fault', [PermissionError, OSError])
def test_root_io_failure_is_unreadable_with_safe_diagnostic(monkeypatch, tmp_path, caplog, fault):
    original = Path.stat
    failure = fault('private-path-and-identity-must-not-be-logged')

    def stat(path, *args, **kwargs):
        if path == tmp_path:
            raise failure
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', stat)
    with pytest.raises(ExecutionRootUnavailable) as raised:
        validate_execution_root(ExecutionRoot(tmp_path))
    assert raised.value.public() == {'code': 'execution_root_unavailable', 'reason': 'unreadable'}
    assert raised.value.__cause__ is failure
    assert 'execution_root_check_failed' in caplog.text and fault.__name__ in caplog.text
    assert str(failure) not in caplog.text


@pytest.mark.parametrize('fault', [PermissionError, OSError, FileNotFoundError])
def test_marker_read_failure_preserves_identity_vs_io_classification(monkeypatch, tmp_path, caplog, fault):
    root = tmp_path / 'execution'
    root.mkdir()
    location = prepare_workspace(root, data_dir=tmp_path / 'data')
    marker = root / '.naiyue' / 'workspace.json'
    original = Path.read_text
    failure = fault('private-marker-detail')

    def read(path, *args, **kwargs):
        if path == marker:
            raise failure
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ExecutionRootUnavailable) as raised:
        validate_execution_root(ExecutionRoot(root), location.workspace_id)
    expected = 'wrong_project' if fault is FileNotFoundError else 'unreadable'
    assert raised.value.reason == expected and raised.value.__cause__ is failure
    assert 'execution_identity_unavailable' in caplog.text and fault.__name__ in caplog.text
    assert str(failure) not in caplog.text


def test_root_type_uses_one_stat_without_second_io(monkeypatch, tmp_path):
    original = Path.stat
    reads = []

    def stat(path, *args, **kwargs):
        if path == tmp_path:
            reads.append(path)
            if len(reads) > 1:
                raise PermissionError('second IO must not happen')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', stat)
    validate_execution_root(ExecutionRoot(tmp_path))
    assert reads == [tmp_path]
    assert not (tmp_path / '.naiyue').exists()


@pytest.mark.parametrize('kind', ['directory', 'file', 'missing', 'file_parent', 'worktree'])
def test_real_path_type_and_registration_admission(tmp_path, kind):
    root = tmp_path / 'execution'
    if kind in ('directory', 'worktree'):
        root.mkdir()
    elif kind in ('file', 'file_parent'):
        root.write_text('sentinel')
        if kind == 'file_parent':
            root = root / 'child'
    execution = ExecutionRoot(root, kind='worktree' if kind == 'worktree' else 'local')
    expected = {'directory': None, 'file': 'not_directory', 'missing': 'missing',
                'file_parent': 'not_directory', 'worktree': 'not_registered'}[kind]
    if expected is None:
        validate_execution_root(execution)
    else:
        with pytest.raises(ExecutionRootUnavailable) as raised:
            validate_execution_root(execution)
        assert raised.value.reason == expected


@pytest.mark.parametrize('identity', ['valid', 'missing', 'malformed', 'different'])
def test_real_project_identity_admission(tmp_path, identity):
    root = tmp_path / 'execution'
    root.mkdir()
    location = prepare_workspace(root, data_dir=tmp_path / 'data')
    marker = root / '.naiyue' / 'workspace.json'
    if identity == 'missing':
        marker.unlink()
    elif identity == 'malformed':
        marker.write_text('not json')
    if identity == 'valid':
        validate_execution_root(ExecutionRoot(root), location.workspace_id)
    else:
        with pytest.raises(ExecutionRootUnavailable) as raised:
            validate_execution_root(ExecutionRoot(root), 'other' if identity == 'different' else location.workspace_id)
        assert raised.value.reason == 'wrong_project'


def test_directoryless_chat_still_admits():
    assert validate_execution_root(None) is None


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('reason', ['missing', 'unreadable'])
def test_shell_rejects_lost_root_after_permit_without_spawn(monkeypatch, tmp_path, background, reason):
    from naiyue_test_agent.tools import create_session_bash_tool
    from naiyue_test_agent.execution import host_shell as shell
    from test_shell_task_settings import runtime_for
    monkeypatch.setattr(shell.ShellAdapter, 'resolve_executable', lambda *_: '/synthetic/interpreter')
    monkeypatch.setattr(shell.ShellAdapter, 'probe_version', lambda *_: 'synthetic-version')
    calls = []
    async def run(*args, **kwargs):
        calls.append(True)
        return ProcessOutcome(0, 'exited', True, True, True, True, 0)
    monkeypatch.setattr(HostProcessRunner, 'run', run)
    root = tmp_path / 'execution'
    root.mkdir()
    spec = create_session_bash_tool()
    args = {'command': 'must not run', 'background': background, 'timeout': None}
    runtime = runtime_for(spec, args, root)
    if reason == 'missing':
        root.rmdir()
    else:
        deny_execution_root(monkeypatch, root)
    with pytest.raises(ToolException, match=f'^execution_root_unavailable: {reason}$'):
        asyncio.run(spec.tool.coroutine(**args, runtime=runtime))
    assert calls == []
