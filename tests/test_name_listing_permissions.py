"""Name discovery does not grant permission to read or change the listed files."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.tools.permissions import FilePermission
from test_dynamic_tools import DynamicModel
from test_permission_pipeline import build


@pytest.mark.parametrize('operation,expected', [('list', 'allow'), ('read', 'ask'), ('write', 'ask'), ('search', 'ask')])
def test_hidden_target_has_operation_specific_default(tmp_path, operation, expected):
    target = tmp_path / ('.agents' if operation == 'list' else '.secret')
    if operation == 'list':
        target.mkdir()
    else:
        target.write_text('PRIVATE_BODY')
    permission = FilePermission(operation=operation)
    subject = permission.prepare({'path': str(target)}, {'workspace_root': tmp_path})
    assert permission.default_decision(subject) == expected
    outside = permission.prepare({'path': str(tmp_path.parent / '.outside')}, {'workspace_root': tmp_path})
    assert permission.default_decision(outside) == 'ask'


@async_test
@pytest.mark.parametrize('name,args', [('ls', {}), ('find', {'pattern': '*'})])
@pytest.mark.parametrize('external', [False, True])
async def test_listed_hidden_names_need_only_directory_scope(monkeypatch, tmp_path, name, args, external):
    root = tmp_path / 'work'
    root.mkdir()
    target = tmp_path / 'outside' if external else root / '.agents'
    target.mkdir()
    (target / '.env').write_text('PRIVATE_BODY')
    (target / '.nested').mkdir()
    (target / 'key.pem').write_text('PRIVATE_KEY')
    spec = next(s for s in create_builtin_tools(AppSettings().tools) if s.tool.name == name)
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[{
        'name': name, 'id': 'names', 'args': {**args, 'path': str(target)}}]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [spec])
    try:
        sid = await app.acreate_workspace_session('A', root)
        result = await app.arun(sid, [('user', 'List the directory names.')])
        if external:
            assert len(result['approval_requests']) == 1
            assert not any(isinstance(m, ToolMessage) for m in result['messages'])
            result = await app.aresume_tool_approval(sid, ['approve'])
        else:
            assert not result.get('approval_requests')
        message, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        assert message.status == 'success'
        assert {'.env', '.nested/', 'key.pem'} <= set(message.content.splitlines())
        assert 'unapproved' not in message.content and 'PRIVATE_' not in message.content
    finally:
        await app.aclose()


@async_test
@pytest.mark.parametrize('name,args', [
    ('read', {'path': '.env'}),
    ('write', {'path': '.env', 'content': 'replacement'}),
    ('grep', {'path': '.env', 'pattern': 'PRIVATE_BODY'}),
])
async def test_listing_a_name_does_not_authorize_its_content(monkeypatch, tmp_path, name, args):
    (tmp_path / '.env').write_text('PRIVATE_BODY')
    tools = [s for s in create_builtin_tools(AppSettings().tools) if s.tool.name in ('ls', name)]
    model = DynamicModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'ls', 'id': 'names', 'args': {}}]),
        AIMessage(content='', tool_calls=[{'name': name, 'id': 'content', 'args': args}]),
    ])
    app = await build(monkeypatch, model, tools)
    try:
        sid = await app.acreate_workspace_session('A', tmp_path)
        result = await app.arun(sid, [('user', 'Inspect the project.')])
        message, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        assert message.status == 'success' and message.content == '.env'
        assert [r['tool_call_id'] for r in result['approval_requests']] == ['content']
        assert (tmp_path / '.env').read_text() == 'PRIVATE_BODY'
    finally:
        await app.aclose()
