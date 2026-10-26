"""A grant prepared before root loss still cannot perform actual file IO."""

from dataclasses import replace
from runtime_context_helpers import ToolAuthorization
from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.tools import create_session_read_tool, create_session_write_tool, create_session_edit_tool, create_session_ls_tool, create_session_find_tool, create_session_grep_tool
from naiyue_test_agent.tools.permissions import FilePermission, execution_environment
from naiyue_test_agent.tools.permission_rules import PermissionRule, match_rules
from test_execution_admission import deny_execution_root


@pytest.mark.parametrize('factory,arguments', [
    (create_session_read_tool, {}),
    (create_session_write_tool, {'content': 'changed'}),
    (create_session_edit_tool, {'edits': [{'oldText': 'sentinel', 'newText': 'changed'}]}),
    (create_session_ls_tool, {}),
    (create_session_find_tool, {'pattern': '*.txt'}),
    (create_session_grep_tool, {'pattern': 'sentinel'}),
])
@pytest.mark.parametrize('reason', ['missing', 'unreadable'])
def test_prepared_file_tool_rechecks_execution_root_before_io(monkeypatch, tmp_path, factory, arguments, reason):
    root = tmp_path / 'execution'
    root.mkdir()
    target = tmp_path / 'external.txt'
    target.write_text('sentinel')
    spec = factory()
    args = {'path': str(target.parent if spec.tool.name in ('ls', 'find') else target), **arguments}
    context = typed_context(identity=RunIdentity(session_id='S', execution_root=ExecutionRoot(root) if root is not None else None))
    subject = spec.permissions.prepare(args, context)
    subject.update(args=args, path_field='path', execution_environment=execution_environment(context))
    context = replace(context, authorization=ToolAuthorization(permit={'call_id': 'C', 'session_id': 'S', 'agent_path': 'root', 'subject': subject}))
    runtime = SimpleNamespace(context=context, tool_call_id='C')
    values = spec.validate_input(args)
    if reason == 'missing':
        root.rmdir()
    else:
        deny_execution_root(monkeypatch, root)
    with pytest.raises(ToolException, match=f'execution_root_unavailable.*{reason}'):
        spec.tool.func(**{key: getattr(values, key) for key in args}, runtime=runtime)
    assert target.read_text() == 'sentinel'


def test_relative_file_target_and_workspace_rule_follow_execution_directory(tmp_path):
    main, execution = tmp_path / 'main', tmp_path / 'execution'
    main.mkdir()
    execution.mkdir()
    for root in (main, execution):
        (root / 'same.txt').write_text(root.name)
    subject = FilePermission().prepare({'path': 'same.txt'}, typed_context(identity=RunIdentity(execution_root=ExecutionRoot(execution) if execution is not None else None, session_id=None)))
    subject.update(tool_name='read', args={'path': 'same.txt'})
    assert subject['workspace'] == str(execution) and subject['resolved'] == str(execution / 'same.txt')
    rule = PermissionRule(tool='read', path='${workspace}/**')
    assert match_rules((rule,), subject, deny=False) is rule
    subject['resolved'] = str(main / 'same.txt')
    assert match_rules((rule,), subject, deny=False) is None
