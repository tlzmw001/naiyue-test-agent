"""Auto is connected to real application preparation, pause and resume."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import ToolSettings, create_session_read_tool
from naiyue_test_agent.tools.permission_rules import PermissionRule
from test_dynamic_tools import DynamicModel, definition, call
from test_auto_permission_review import model, verdict, settings


def build(monkeypatch, primary, fast, detailed, *, specs=None, options=None):
    catalog = ModelCatalog('main', {'main': primary, 'fast': fast, 'detail': detailed})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
    return application.build_local_application('synthetic', tools=specs or [definition(approved=True)],
        settings=options or settings(), system_prompt='', response_format=None)


def test_auto_input_and_no_extra_main_model_messages(monkeypatch, tmp_path):
    (tmp_path / 'AGENTS.md').write_text('PROJECT_REVIEW_RULE')
    from skill_helpers import write_skill
    write_skill(tmp_path, description='SKILL_INDEX_PRIVATE')
    primary, fast, detailed = DynamicModel(responses=[call(), AIMessage(content='done')]), model([verdict()]), model([])
    app = build(monkeypatch, primary, fast, detailed, specs=[definition(approved=True), create_session_read_tool()])
    result = app.run(app.create_workspace_session('main', tmp_path), [('user', 'run probe')])
    assert not result.get('approval_requests') and result['messages'][-1].content == 'done'
    data = json.loads(fast.seen[0][-1].content)
    assert data['human_messages'][0]['content'] == 'run probe'
    assert 'PROJECT_REVIEW_RULE' in data['project_instructions']
    assert 'SKILL_INDEX_PRIVATE' not in json.dumps(data)
    assert data['subject']['side_effects'] == 'unknown'
    assert [m.type for m in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert 'bounded reason' not in str(result['messages']) and len(primary.seen) == 2


def test_two_asks_pause_entire_batch_and_resume_does_not_review_again(monkeypatch):
    executed = []
    fast, detailed = model([verdict('ask')]), model([verdict('ask')])
    primary = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {'value': 'approved'}, 'id': 'ask'},
        {'name': 'ordinary', 'args': {'value': 'ordinary'}, 'id': 'ordinary'}]), AIMessage(content='done')])
    app = build(monkeypatch, primary, fast, detailed,
        specs=[definition(calls=executed, approved=True), definition('ordinary', calls=executed)])
    sid = app.create_session('main')
    paused = app.run(sid, [('user', 'run both')])
    assert [r['tool_call_id'] for r in paused['approval_requests']] == ['ask'] and executed == []
    app.set_permission_mode(sid, 'BypassApprovals')
    result = app.resume_tool_approval(sid, ['approve'])
    assert executed == [('probe', 'approved'), ('ordinary', 'ordinary')]
    assert len(fast.seen) == len(detailed.seen) == 1
    assert result['messages'][-1].content == 'done'


@pytest.mark.parametrize('decision', ['allow', 'deny'])
def test_explicit_rules_do_not_call_review_models(monkeypatch, decision):
    options = settings().model_copy(update={'permissions': settings().permissions.model_copy(update={
        decision: (PermissionRule(tool='probe'),)})})
    fast, detailed = model([]), model([])
    app = build(monkeypatch, DynamicModel(responses=[call(), AIMessage(content='done')]), fast, detailed, options=options)
    result = app.run(app.create_session('main'), [('user', 'run')])
    output = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert output.status == ('success' if decision == 'allow' else 'error')
    assert fast.seen == detailed.seen == []


def test_auto_configuration_requires_both_known_models_before_graph(monkeypatch):
    with pytest.raises(ValueError):
        ToolSettings.model_validate({'permissions': {'mode': 'Auto'}})
    primary = DynamicModel(responses=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('main', {'main': primary}))
    with pytest.raises(ValueError):
        application.build_local_application('synthetic', tools=[], settings=settings(), system_prompt='', response_format=None)
    assert primary.seen == []


def test_switching_to_auto_requires_configuration_but_default_can_use_configured_reviewer(monkeypatch):
    options = settings().model_copy(update={'permissions': settings().permissions.model_copy(update={'mode': 'Default'})})
    fast, detailed = model([verdict()]), model([])
    app = build(monkeypatch, DynamicModel(responses=[call(), AIMessage(content='done')]), fast, detailed, options=options)
    sid = app.create_session('main')
    app.set_permission_mode(sid, 'Auto')
    assert app.run(sid, [('user', 'run')])['messages'][-1].content == 'done'
    assert len(fast.seen) == 1


@pytest.mark.parametrize('agents', [True, False])
def test_project_selection_frozen_within_run_and_refreshed_next_run(monkeypatch, tmp_path, agents):
    from naiyue_test_agent.application import skills
    target = tmp_path / ('AGENTS.md' if agents else 'CLAUDE.md')
    (tmp_path / 'CLAUDE.md').write_text('CLAUDE_FALLBACK')
    target.write_text('PROJECT_OLD')
    reads = []
    original = skills.load_workspace_instructions
    def load(root):
        reads.append(root)
        return original(root)
    monkeypatch.setattr(skills, 'load_workspace_instructions', load)
    fast, detailed = model([verdict('ask'), verdict()]), model([verdict()])
    fast.on_request = lambda number: target.write_text('PROJECT_NEW') if number == 1 else None
    primary = DynamicModel(responses=[call(call_id='one'), AIMessage(content='one'),
                                      call(call_id='two'), AIMessage(content='two')])
    app = build(monkeypatch, primary, fast, detailed)
    sid = app.create_workspace_session('main', tmp_path)
    app.run(sid, [('user', 'run one')])
    assert len(reads) == 1
    first = json.loads(fast.seen[0][-1].content)
    assert 'PROJECT_OLD' in first['project_instructions'] and 'CLAUDE_FALLBACK' not in str(first)
    assert fast.seen[0][-1].content == detailed.seen[0][-1].content
    app.run(sid, [('user', 'run two')])
    assert len(reads) == 2 and 'PROJECT_NEW' in json.loads(fast.seen[1][-1].content)['project_instructions']
    assert app._sessions._sessions[sid]['run_review_project_instructions'] is None


def test_resume_uses_saved_ask_and_refreshes_only_subsequent_batch(monkeypatch, tmp_path):
    target = tmp_path / 'AGENTS.md'
    target.write_text('OLD_RULE')
    fast, detailed = model([verdict('ask'), verdict()]), model([verdict('ask')])
    primary = DynamicModel(responses=[call(call_id='old'), call(call_id='next'), AIMessage(content='done')])
    executed = []
    app = build(monkeypatch, primary, fast, detailed, specs=[definition(calls=executed, approved=True)])
    sid = app.create_workspace_session('main', tmp_path)
    app.run(sid, [('user', 'run both')])
    target.write_text('NEW_RULE')
    result = app.resume_tool_approval(sid, ['approve'])
    assert result['messages'][-1].content == 'done' and len(executed) == 2
    assert len(fast.seen) == 2 and len(detailed.seen) == 1
    before, after = [json.loads(request[-1].content) for request in fast.seen]
    assert before['current_call']['id'] == 'old' and 'OLD_RULE' in before['project_instructions']
    assert after['current_call']['id'] == 'next' and 'NEW_RULE' in after['project_instructions']
    assert [h['content'] for h in after['human_messages']] == ['run both']


def test_mode_switched_during_review_applies_to_next_batch(monkeypatch):
    fast, detailed = model([verdict()]), model([])
    primary = DynamicModel(responses=[call(call_id='old'), call(call_id='next'), AIMessage(content='done')])
    app = build(monkeypatch, primary, fast, detailed)
    sid = app.create_session('main')
    fast.on_request = lambda _: app.set_permission_mode(sid, 'NoAsk')
    result = app.run(sid, [('user', 'run twice')])
    outputs = [m for m in result['messages'] if isinstance(m, ToolMessage)]
    assert [(m.tool_call_id, m.status) for m in outputs] == [('old', 'success'), ('next', 'error')]
    assert len(fast.seen) == 1 and app.get_permission_mode(sid) == 'NoAsk'


def test_control_messages_keep_human_provenance_when_optional_observation_fails(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime.control_observation import ControlObserver
    def fail(*args):
        raise RuntimeError('PRIVATE_OBSERVATION')
    monkeypatch.setattr(ControlObserver, 'prepare_messages', fail)
    primary = DynamicModel(responses=[call(call_id='one'), call(call_id='two'), AIMessage(content='first end'),
                                      call(call_id='three'), AIMessage(content='done')])
    fast, detailed = model([verdict(), verdict(), verdict()]), model([])
    app = build(monkeypatch, primary, fast, detailed)
    sid = app.create_session('main')
    def queue(number):
        if number == 1:
            app.steer(sid, 'steering task')
            app.follow_up(sid, 'follow-up task')
    primary.on_request = queue
    source = HumanMessage(content='initial task', id='human', additional_kwargs={'custom': 'preserved'})
    result = app.run(sid, [source])
    inputs = [json.loads(request[-1].content) for request in fast.seen]
    assert [[h['content'] for h in data['human_messages']] for data in inputs] == [
        ['initial task'], ['initial task', 'steering task'], ['initial task', 'steering task', 'follow-up task']]
    assert result['messages'][0].id == 'human' and result['messages'][0].additional_kwargs == {
        'custom': 'preserved', 'naiyue_human_input': True}
    assert source.additional_kwargs == {'custom': 'preserved'}
    assert 'observation_failed' in caplog.text and 'PRIVATE_OBSERVATION' not in caplog.text


def test_invalid_arguments_and_session_allow_bypass_review(monkeypatch):
    fast, detailed = model([verdict('ask')]), model([verdict('ask')])
    primary = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {'value': 4}, 'id': 'invalid'},
        {'name': 'probe', 'args': {'value': 'same'}, 'id': 'ask'}]), AIMessage(content='done'),
        call(value='same', call_id='remembered'), AIMessage(content='done')])
    app = build(monkeypatch, primary, fast, detailed)
    sid = app.create_session('main')
    paused = app.run(sid, [('user', 'run')])
    assert [r['tool_call_id'] for r in paused['approval_requests']] == ['ask']
    done = app.resume_tool_approval(sid, ['approve'], remember_approved=['ask'])
    assert [(m.tool_call_id, m.status) for m in done['messages'] if isinstance(m, ToolMessage)] == [
        ('invalid', 'error'), ('ask', 'success')]
    result = app.run(sid, [('user', 'run again')])
    assert not result.get('approval_requests') and len(fast.seen) == len(detailed.seen) == 1
