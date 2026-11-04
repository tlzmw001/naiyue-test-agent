"""Every file tool dispatches to the injected provider, without a host directory."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from naiyue_test_agent.execution_roots import ExecutionRoot
from naiyue_test_agent.contracts.run_context import RunIdentity, RunScope, RunCapabilities, RuntimeContext, ToolAuthorization
from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permissions import execution_environment, PermissionMechanismError
from naiyue_test_agent.tools.permission_rules import PermissionSettings


class RecordingFiles:
    owner_session_id = 'S'

    def __init__(self, root):
        from naiyue_test_agent.execution.host_files import HostFileSystem
        from naiyue_test_agent.execution_roots import ExecutionRoot
        self.environment=HostFileSystem(ExecutionRoot(root),None,session_id='S').environment
        self.operations=[]
    def validate(self): self.operations.append('validate')
    def prepare_target(self, path, operation):
        target = str(self.environment.path(self.environment.root) / path)
        return dict(workspace=self.environment.root, requested=target, resolved=target, operation=operation)
    def verify_target(self, subject):
        self.operations.append('verify')
        return subject['resolved']
    def read(self, *args, **kwargs): self.operations.append('read'); return 'provider read'
    def write(self, subject, *args, **kwargs): self.operations.append('write'); self.subject=subject; return 'provider write', {}
    def edit(self, subject, *args, **kwargs): self.operations.append('edit'); self.subject=subject; return 'provider edit', {}
    def list(self, *args, **kwargs): self.operations.append('ls'); return 'provider ls'
    async def find(self, *args, **kwargs): self.operations.append('find'); return 'provider find'
    async def grep(self, *args, **kwargs): self.operations.append('grep'); return 'provider grep'


@pytest.mark.parametrize('name,args', [
    ('read', {'path': 'doc.txt'}), ('write', {'path': 'doc.txt', 'content': 'content'}),
    ('edit', {'path': 'doc.txt', 'edits': [{'oldText': 'before', 'newText': 'after'}]}),
    ('ls', {}), ('find', {'pattern': '*.txt'}), ('grep', {'pattern': 'content'})])
def test_six_tools_use_provider_without_host_io(tmp_path, name, args):
    spec = next(tool for tool in create_builtin_tools(AppSettings().tools) if tool.tool.name == name)
    files = RecordingFiles(tmp_path)
    context = RuntimeContext(RunScope(RunIdentity('S', 'R', execution_root=ExecutionRoot(tmp_path)),
        capabilities=RunCapabilities(files=files), environment=files.environment))
    values = spec.validate_input(args)
    prepared_args = values.model_dump()
    subject = spec.permissions.prepare(prepared_args, context)
    subject.update(args=prepared_args, tool_name=name, path_field='path', execution_environment=execution_environment(context))
    permit = dict(call_id='C', session_id='S', agent_path='root', tool_name=name, subject=subject)
    runtime = SimpleNamespace(context=replace(context, authorization=ToolAuthorization(permit=permit,
        permission_rules=PermissionSettings(), session_allow=[], permission_mode='Default')), tool_call_id='C')
    if spec.tool.coroutine is not None:
        result = asyncio.run(spec.tool.coroutine(runtime=runtime, **prepared_args))
    else:
        result = spec.tool.func(runtime=runtime, **prepared_args)
    expected = (f'provider {name}', {}) if name in ('write', 'edit') else f'provider {name}'
    assert result == expected
    assert name in files.operations and 'validate' in files.operations and 'verify' in files.operations
    if name in ('write','edit'):
        assert files.subject == subject


def test_reviewed_target_cannot_use_another_environment_root(tmp_path):
    from naiyue_test_agent.tools.permissions import require_file_target
    files = RecordingFiles(tmp_path)
    subject = dict(workspace='/another/project', operation='read', args={'path': 'doc.txt'}, path_field='path',
        execution_environment=files.environment.public())
    context = RuntimeContext(RunScope(RunIdentity('S', execution_root=ExecutionRoot(tmp_path)), capabilities=RunCapabilities(files=files),
        environment=files.environment), authorization=ToolAuthorization(permit=dict(call_id='C', session_id='S',
        agent_path='root', subject=subject)))
    with pytest.raises(PermissionMechanismError, match='^Tool execution directory differs from its reviewed subject$'):
        require_file_target(SimpleNamespace(context=context, tool_call_id='C'), 'doc.txt', operation='read')
    assert 'verify' not in files.operations
