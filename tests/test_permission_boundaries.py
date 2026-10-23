"""Permission composition covers target aliases, batching and internal grants."""

from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from native_application_helpers import async_test

from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permissions import (
    FilePermission, ToolPermission, OneUseApprovalGrants, PermissionMechanismError,
)
from naiyue_test_agent.agent_runtime.tool_execution.authorization import ExecutionAuthorizationMiddleware
from naiyue_test_agent.tools.permission_rules import PermissionSettings
from test_permission_pipeline import build, call
from test_dynamic_tools import DynamicModel, definition


def outputs(result):
    return {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}


@pytest.mark.parametrize('tool_name', ['read', 'write', 'edit'])
@pytest.mark.parametrize('directory_link', [False, True])
@async_test
async def test_file_and_directory_aliases_use_real_target_without_replacing_link(monkeypatch, tmp_path, tool_name, directory_link):
    root, outside = tmp_path / 'workspace', tmp_path / 'outside'
    root.mkdir(); outside.mkdir()
    target = outside / 'note.txt'
    target.write_text('before')
    link = root / ('alias' if directory_link else 'alias.txt')
    link.symlink_to(outside if directory_link else target, target_is_directory=directory_link)
    path = link / 'note.txt' if directory_link else link
    args = {'path': str(path)}
    if tool_name == 'write':
        args['content'] = 'after'
    elif tool_name == 'edit':
        args['edits'] = [{'oldText': 'before', 'newText': 'after'}]
    spec = next(s for s in create_builtin_tools(AppSettings().tools) if s.tool.name == tool_name)
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[{'name': tool_name, 'args': args, 'id': 'op'}]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [spec])
    sid = await app.acreate_workspace_session('A', root)
    paused = await app.arun(sid, [('user', 'operate on the alias')])
    assert paused['approval_requests'][0]['target'] == str(target)
    assert target.read_text() == 'before' and link.is_symlink()
    result = await app.aresume_tool_approval(sid, ['approve'])
    assert outputs(result)['op'].status == 'success'
    assert target.read_text() == ('before' if tool_name == 'read' else 'after')
    assert link.is_symlink()


@pytest.mark.parametrize('name,args', [('ls', {}), ('find', {'pattern': '*'}), ('grep', {'pattern': 'NEEDLE'})])
@pytest.mark.parametrize('explicit_allow', [False, True])
@async_test
async def test_directory_permission_filters_deny_and_keeps_content_checks(monkeypatch, tmp_path, name, args, explicit_allow):
    root, directory = tmp_path / 'workspace', tmp_path / 'outside'
    root.mkdir(); directory.mkdir()
    for filename, body in [('ordinary.txt', 'NEEDLE_OK'), ('denied.txt', 'NEEDLE_DENIED'),
                           ('.private', 'NEEDLE_PRIVATE'), ('secret.key', 'NEEDLE_KEY')]:
        (directory / filename).write_text(body)
    spec = next(s for s in create_builtin_tools(AppSettings().tools) if s.tool.name == name)
    options = {'deny': [{'tool': name, 'path': str(directory / 'denied.txt')}]}
    if explicit_allow:
        options['allow'] = [{'tool': name, 'path': str(directory) + '/**'}]
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': name, 'args': {**args, 'path': str(directory)}, 'id': 'search'}]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [spec], **options)
    sid = await app.acreate_workspace_session('A', root)
    result = await app.arun(sid, [('user', 'search the directory')])
    if not explicit_allow:
        assert result['approval_requests'] and not outputs(result)
        result = await app.aresume_tool_approval(sid, ['approve'])
    else:
        assert not result.get('approval_requests')
    message = outputs(result)['search']
    assert message.status == 'success' and 'ordinary.txt' in message.content
    assert 'denied.txt: deny' in message.content and 'NEEDLE_DENIED' not in message.content
    if explicit_allow or name in ('ls', 'find'):
        assert '.private' in message.content and 'secret.key' in message.content
        assert 'unapproved_private_or_external' not in message.content
    else:
        assert '.private: unapproved_private_or_external' in message.content
        assert 'secret.key: unapproved_private_or_external' in message.content
        assert 'NEEDLE_PRIVATE' not in message.content and 'NEEDLE_KEY' not in message.content


@async_test
async def test_schema_error_waits_for_batch_approval_without_reading_first(monkeypatch, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-external.txt')
    outside.write_text('approved')
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        call('unused', 'invalid', offset=0), call(outside, 'valid')]), AIMessage(content='done')])
    app = await build(monkeypatch, model)
    sid = await app.acreate_workspace_session('A', tmp_path)
    paused = await app.arun(sid, [('user', 'read')])
    assert [item['tool_call_id'] for item in paused['approval_requests']] == ['valid']
    assert not outputs(paused)
    result = await app.aresume_tool_approval(sid, ['approve'])
    assert outputs(result)['invalid'].status == 'error'
    assert 'Invalid read arguments' in outputs(result)['invalid'].content
    assert 'runtime' not in outputs(result)['invalid'].content
    assert outputs(result)['valid'].content == 'approved'


@async_test
async def test_custom_tool_without_declaration_requires_review(monkeypatch):
    spec = replace(definition(), permissions=ToolPermission())
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[{'name': 'probe', 'args': {'value': 'v'}, 'id': 'c'}]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [spec])
    sid = await app.acreate_session('A')
    paused = await app.arun(sid, [('user', 'run')])
    assert paused['approval_requests'] and not outputs(paused)
    result = await app.aresume_tool_approval(sid, ['approve'])
    assert outputs(result)['c'].content == 'v'


@pytest.mark.parametrize('mode', ['NoAsk', 'BypassApprovals'])
@async_test
async def test_modes_cannot_override_explicit_deny(monkeypatch, tmp_path, mode):
    path = tmp_path / 'secret'
    path.write_text('PRIVATE_DENIED')
    app = await build(monkeypatch, DynamicModel(responses=[AIMessage(content='', tool_calls=[call(path)]), AIMessage(content='done')]),
        mode=mode, deny=[{'tool': 'read', 'path': str(path)}], allow=[{'tool': 'read', 'path': str(tmp_path) + '/**'}])
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'read')])
    assert outputs(result)['c1'].status == 'error'
    assert 'PRIVATE_DENIED' not in outputs(result)['c1'].content
    assert not result.get('approval_requests')


def test_one_use_grant_checks_full_binding_and_cannot_be_replayed():
    from naiyue_test_agent.tools.permissions import permission_binding
    call_data = {'id': 'c', 'name': 'read', 'args': {'path': 'a'}, 'subject': {'operation': 'read'}}
    context = typed_context(identity=RunIdentity(session_id='s', agent_path='root'))
    binding = permission_binding(context.scope.identity, 'batch', call_data)
    grants = OneUseApprovalGrants({'c': binding})
    for key, value in [('session_id', 'other'), ('agent_path', 'child'), ('batch_id', 'other'),
                       ('args', {'path': 'b'}), ('subject', {'operation': 'write'})]:
        assert not grants.consume('c', {**binding, key: value})
    assert grants.consume('c', binding)
    assert not grants.consume('c', binding)


def test_missing_saved_permission_is_a_mechanism_failure():
    request = SimpleNamespace(state={'tool_execution': {}}, tool_call={'id': 'c', 'name': 'read', 'args': {'path': 'a'}})
    with pytest.raises(PermissionMechanismError):
        ExecutionAuthorizationMiddleware(PermissionSettings())._authorize(request)


def test_selected_hidden_workspace_is_valid_but_target_policy_remains_conservative(tmp_path):
    from naiyue_test_agent.tools.read import resolve_workspace_root
    root = tmp_path / '.private'
    root.mkdir()
    assert resolve_workspace_root(root) == root.resolve()
    permission = FilePermission()
    normal = permission.prepare({'path': 'notes.txt'}, typed_context(identity=RunIdentity(execution_root=ExecutionRoot(root) if root is not None else None, session_id=None)))
    hidden = permission.prepare({'path': '.secret'}, typed_context(identity=RunIdentity(execution_root=ExecutionRoot(root) if root is not None else None, session_id=None)))
    assert permission.default_decision(normal) == 'allow'
    assert permission.default_decision(hidden) == 'ask'
