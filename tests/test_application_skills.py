"""Skill index, resource read, and per-run context must be wired together."""

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import logging

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from naiyue_test_agent import application
from naiyue_test_agent.application import session_service
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog
from skill_helpers import write_skill
from test_session_system_prompt import RecordingModel, build_app, system_text
from test_context_compaction import _model


def _read_call(path, call_id='read-1', **extra):
    return AIMessage(content='', tool_calls=[{
        'name': 'read', 'args': {'path': str(path), **extra}, 'id': call_id,
    }])


@pytest.mark.parametrize('source', ['agents', 'claude'])
@async_test
async def test_both_indexes_are_visible_and_body_enters_next_model_call(monkeypatch, tmp_path, caplog, source):
    paths = {name: write_skill(tmp_path, name, body=f'BODY_{name}')
             for name in ('agents', 'claude')}
    chosen = paths[source]
    reference = chosen.parent / 'references/rules.md'
    reference.parent.mkdir()
    reference.write_text('REFERENCE_BODY', encoding='utf-8')
    model = RecordingModel(responses=[_read_call(chosen), _read_call(reference, 'ref-1'),
                                     AIMessage(content='done')])
    model.metadata = _model([], window=10000).metadata
    read = create_session_read_tool()
    app = await build_app(monkeypatch, model, tools=[read])
    session = await app.acreate_workspace_session('A', tmp_path)
    with caplog.at_level(logging.INFO):
        result = await app.arun(session, [('user', f'use the {source} skill')])
    prompt = system_text(model.inputs[0])
    assert '<available_skills' in prompt and 'default_source="agents"' in prompt
    assert str(paths['agents']) in prompt and str(paths['claude']) in prompt
    assert 'BODY_' not in prompt
    tool_messages = [msg for msg in result['messages'] if isinstance(msg, ToolMessage)]
    assert [(msg.tool_call_id, msg.status) for msg in tool_messages] == [
        ('read-1', 'success'), ('ref-1', 'success'),
    ]
    assert tool_messages[0].content == chosen.read_text(encoding='utf-8')
    assert tool_messages[1].content == 'REFERENCE_BODY'
    assert tool_messages[0] in model.inputs[1]
    assert tool_messages[1] in model.inputs[2]
    assert not result.get('approval_requests')
    preflight = [r.model_context_preflight for r in caplog.records if hasattr(r, 'model_context_preflight')]
    assert preflight[0]['estimated_input_tokens'] == count_tokens_approximately(model.inputs[0], tools=[read.tool])


@pytest.mark.parametrize('target_kind', ['env', 'key', 'hidden', 'external_link', 'changed_root'])
@async_test
async def test_resource_scope_never_bypasses_sensitive_or_external_paths(monkeypatch, tmp_path, target_kind):
    root = tmp_path / 'workspace'
    root.mkdir()
    skill = write_skill(root)
    target = skill.parent / {'env': '.env', 'key': 'private.key', 'hidden': '.hidden',
                            'external_link': 'link.txt', 'changed_root': 'SKILL.md'}[target_kind]
    outside = tmp_path / 'outside.txt'
    outside.write_text('PRIVATE_EXTERNAL', encoding='utf-8')
    if target_kind == 'external_link':
        target.symlink_to(outside)
    elif target_kind != 'changed_root':
        target.write_text('PRIVATE_TARGET', encoding='utf-8')

    @tool
    def alter_source() -> str:
        """Change a controlled test resource after discovery."""
        if target_kind == 'changed_root':
            moved = root / 'moved'
            skill.parent.rename(moved)
            skill.parent.symlink_to(tmp_path, target_is_directory=True)
        return 'changed'

    model = RecordingModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'alter_source', 'args': {}, 'id': 'alter'}]),
        _read_call(target), AIMessage(content='handled'),
    ])
    app = await build_app(monkeypatch, model, tools=[create_session_read_tool(), ToolSpec(alter_source, alter_source.args_schema, permissions=ToolPermission(default='allow'))])
    session = await app.acreate_workspace_session('A', root)
    await app.aset_permission_mode(session, 'NoAsk')
    result = await app.arun(session, [('user', 'test read boundary')])
    outcome = next(msg for msg in result['messages'] if isinstance(msg, ToolMessage)
                   and msg.tool_call_id == 'read-1')
    assert outcome.status == 'error' and 'PRIVATE_' not in outcome.content
    assert outcome in model.inputs[-1]


@async_test
async def test_index_is_frozen_but_body_is_read_live_then_index_refreshes(monkeypatch, tmp_path):
    path = write_skill(tmp_path, description='DESC_ONE', body='BODY_ONE')

    @tool
    def modify_skill() -> str:
        """Modify a local Skill fixture during this run."""
        write_skill(tmp_path, description='DESC_TWO', body='BODY_TWO')
        return 'changed'

    model = RecordingModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'modify_skill', 'args': {}, 'id': 'modify'}]),
        _read_call(path), AIMessage(content='first done'), AIMessage(content='next done'),
    ])
    app = await build_app(monkeypatch, model, tools=[create_session_read_tool(), ToolSpec(modify_skill, modify_skill.args_schema, permissions=ToolPermission(default='allow'))])
    session = await app.acreate_workspace_session('A', tmp_path)
    first = await app.arun(session, [('user', 'modify and read')])
    assert all('DESC_ONE' in system_text(items) and 'DESC_TWO' not in system_text(items)
               for items in model.inputs[:3])
    assert any(isinstance(msg, ToolMessage) and 'BODY_TWO' in msg.content for msg in first['messages'])
    await app.arun(session, [('user', 'next')])
    assert 'DESC_TWO' in system_text(model.inputs[-1])


@async_test
async def test_workspaces_do_not_share_indexes_or_resource_permissions(monkeypatch, tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir(); b.mkdir()
    first = write_skill(a, description='WORKSPACE_A')
    second = write_skill(b, description='WORKSPACE_B', body='BODY_B')
    model = RecordingModel(responses=[_read_call(second), AIMessage(content='denied'),
                                     _read_call(second), AIMessage(content='allowed')])
    app = await build_app(monkeypatch, model, tools=[create_session_read_tool()])
    session_a = await app.acreate_workspace_session('A', a)
    await app.aset_permission_mode(session_a, 'NoAsk')
    result_a = await app.arun(session_a, [('user', 'read')])
    result_b = await app.arun(await app.acreate_workspace_session('A', b), [('user', 'read')])
    assert 'WORKSPACE_A' in system_text(model.inputs[0]) and str(second) not in system_text(model.inputs[0])
    assert 'WORKSPACE_B' in system_text(model.inputs[2]) and str(first) not in system_text(model.inputs[2])
    assert next(msg for msg in result_a['messages'] if isinstance(msg, ToolMessage)).status == 'error'
    assert next(msg for msg in result_b['messages'] if isinstance(msg, ToolMessage)).status == 'success'


@async_test
async def test_approval_resume_reinstalls_resource_scope_and_refreshes_index(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    skill = write_skill(root, description='BEFORE_PAUSE')
    outside = tmp_path / 'outside.txt'
    outside.write_text('approved content', encoding='utf-8')
    model = RecordingModel(responses=[_read_call(outside, 'outside'), _read_call(skill),
                                     AIMessage(content='done')])
    app = await build_app(monkeypatch, model, tools=[create_session_read_tool()])
    session = await app.acreate_workspace_session('A', root)
    assert (await app.arun(session, [('user', 'read')]))['approval_requests']
    write_skill(root, description='AFTER_PAUSE')
    result = await app.aresume_tool_approval(session, ['approve'])
    assert 'AFTER_PAUSE' in system_text(model.inputs[1])
    assert next(msg for msg in result['messages'] if isinstance(msg, ToolMessage)
                and msg.tool_call_id == 'read-1').status == 'success'


@async_test
async def test_no_read_tool_means_no_unloadable_skill_advertisement(monkeypatch, tmp_path):
    write_skill(tmp_path)
    model = RecordingModel(responses=[AIMessage(content='done')])
    app = await build_app(monkeypatch, model)
    await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'hello')])
    assert 'available_skills' not in system_text(model.inputs[0])


@async_test
async def test_reference_pagination_and_encoding_failure_remain_tool_results(monkeypatch, tmp_path):
    skill = write_skill(tmp_path)
    reference = skill.parent / 'large.txt'
    reference.write_text(''.join(f'line {number}\n' for number in range(1, 3001)), encoding='utf-8')
    binary = skill.parent / 'other-encoding.txt'
    binary.write_bytes(b'not-utf8\xff')
    model = RecordingModel(responses=[_read_call(reference, offset=2001, limit=3),
                                     _read_call(binary, 'bad'), AIMessage(content='handled')])
    app = await build_app(monkeypatch, model, tools=[create_session_read_tool()])
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'read references')])
    outputs = [msg for msg in result['messages'] if isinstance(msg, ToolMessage)]
    assert outputs[0].status == 'success'
    assert outputs[0].content == 'line 2001\nline 2002\nline 2003\n[read: lines 2001-2003; next_offset=2004]\n[read: stopped by 3 line page limit; continue with the same path, offset=2004, limit=3]'
    assert outputs[1].status == 'error' and 'UTF-8' in outputs[1].content
    assert outputs[1] in model.inputs[-1]


@async_test
async def test_model_cannot_supply_resource_permissions_as_tool_arguments(monkeypatch, tmp_path):
    path = write_skill(tmp_path)
    model = RecordingModel(responses=[_read_call(path, read_resource_roots=[str(path.parent)]),
                                     AIMessage(content='handled')])
    read = create_session_read_tool()
    assert 'read_resource_roots' not in read.tool.tool_call_schema.model_json_schema()['properties']
    app = await build_app(monkeypatch, model, tools=[read])
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'read')])
    output = next(msg for msg in result['messages'] if isinstance(msg, ToolMessage))
    assert output.status == 'error' and 'Invalid read arguments' in output.content


@async_test
async def test_disappearing_optional_resource_after_discovery_is_a_tool_error(monkeypatch, tmp_path):
    path = write_skill(tmp_path)
    model = RecordingModel(responses=[_read_call(path), AIMessage(content='handled')])
    app = await build_app(monkeypatch, model, tools=[create_session_read_tool()])
    original = session_service.ainvoke_agent_run_with_resources

    async def invoke_after_move(*args, **kwargs):
        path.parent.rename(tmp_path / 'moved-resource')
        return await original(*args, **kwargs)

    monkeypatch.setattr(session_service, 'ainvoke_agent_run_with_resources', invoke_after_move)
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'read')])
    output = next(msg for msg in result['messages'] if isinstance(msg, ToolMessage))
    assert output.status == 'error' and output in model.inputs[-1]
    assert result['messages'][-1].content == 'handled'


@async_test
async def test_overlapping_registered_roots_do_not_shadow_specific_skill_permissions(monkeypatch, tmp_path):
    broad = write_skill(tmp_path, name='root-skill', directory='a')
    broad.replace(tmp_path / 'SKILL.md')
    broad.parent.rmdir()
    broad.parent.symlink_to(tmp_path, target_is_directory=True)
    narrow = write_skill(tmp_path, name='demo', directory='z')
    model = RecordingModel(responses=[_read_call(narrow), AIMessage(content='done')])
    app = await build_app(monkeypatch, model, tools=[create_session_read_tool()])
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'read demo')])
    output = next(msg for msg in result['messages'] if isinstance(msg, ToolMessage))
    assert output.status == 'success' and 'BODY_MARKER' in output.content


@async_test
async def test_skill_index_is_in_compaction_and_overflow_request_budget(monkeypatch, tmp_path, caplog):
    from test_context_compaction import _model
    from test_context_overflow_recovery import _configured_model, _status, _history

    write_skill(tmp_path, description='BUDGET_RULE')
    read = create_session_read_tool()
    first_model = _model([AIMessage(content='old answer')], window=10000)
    compact_model = _model([AIMessage(content='new answer')], window=1000, reserve=950, keep=5,
                           summaries=[AIMessage(content='brief summary')])
    recovery_model = _configured_model([_status(), AIMessage(content='recovered')])
    models = {'A': first_model, 'B': compact_model, 'C': recovery_model}
    monkeypatch.setattr(application, 'load_model_catalog', lambda _path: ModelCatalog('A', models))
    app = await build_application('synthetic.json', tools=[read], system_prompt='BASE', response_format=None)
    session = await app.acreate_workspace_session('A', tmp_path)
    old = await app.arun(session, [('user', 'OLD_FACT_' + 'x' * 200)])
    await app.aselect_model(session, 'B')
    with caplog.at_level(logging.INFO):
        await app.arun(session, [('user', 'new')])
        other = await app.acreate_workspace_session('C', tmp_path)
        await app.arun(other, _history())
    assert len(compact_model.summary_inputs) == len(recovery_model.summary_inputs) == 1
    assert 'available_skills' in system_text(compact_model.normal_inputs[0])
    assert system_text(recovery_model.normal_inputs[0]) == system_text(recovery_model.normal_inputs[1])
    starts = [record.context_compaction for record in caplog.records if hasattr(record, 'context_compaction')
              and record.context_compaction['event'] == 'context_compaction_started']
    expected = count_tokens_approximately([compact_model.normal_inputs[0][0], *old['messages'], HumanMessage(content='new')], tools=[read.tool])
    assert starts[0]['estimated_input_tokens'] == expected
