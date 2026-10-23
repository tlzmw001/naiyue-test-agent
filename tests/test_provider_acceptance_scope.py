"""Model-generated operations cannot escape the disposable acceptance fixture."""

from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from pathlib import Path
import pytest

from scripts.provider_acceptance_scope import FixturePermission, FixtureBoundaryStop
from naiyue_test_agent.tools.permissions import ToolPermission, FilePermission


def test_shell_guard_blocks_unapproved_command_before_allow_policy(tmp_path):
    guard = FixturePermission('bash', ToolPermission(default='allow'), tmp_path)
    with pytest.raises(FixtureBoundaryStop):
        guard.prepare({'command': 'printf UNAUTHORIZED'}, typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None)))
    subject = guard.prepare({'command': 'printf AUTO_OK'}, typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None)))
    assert guard.default_decision(subject) == 'allow'


def test_file_guard_checks_real_and_requested_targets_before_approval(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    guard = FixturePermission('read', FilePermission(operation='read'), work)
    context = typed_context(identity=RunIdentity(execution_root=ExecutionRoot(work) if work is not None else None, session_id=None))
    with pytest.raises(FixtureBoundaryStop):
        guard.prepare({'path': str(tmp_path / 'secret.txt')}, context)
    (work / 'link.txt').symlink_to(tmp_path / 'secret.txt')
    with pytest.raises(FixtureBoundaryStop):
        guard.prepare({'path': 'link.txt'}, context)
    subject = guard.prepare({'path': 'synthetic.txt'}, context)
    assert guard.default_decision(subject) == 'allow'
