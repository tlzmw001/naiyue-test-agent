"""Review roles and child checkpoints use formal assembly before live execution."""

import asyncio
import pytest

from langchain_core.messages import AIMessage
from naiyue_test_agent.model_access import (ModelCatalog, MODEL_LIMITS_METADATA_KEY,
    COMPACTION_RESERVE_METADATA_KEY, COMPACTION_KEEP_RECENT_METADATA_KEY)
from scripts import provider_acceptance_agents as agents, provider_acceptance_cases as shared
from scripts import provider_acceptance_context as context
from scripts.provider_acceptance_cases import CaseEnvironment
from scripts.provider_acceptance_budget import RequestBudget
from test_provider_acceptance_core import AcceptanceModel
from test_async_application import call


def prepare(monkeypatch, tmp_path, replies):
    model = AcceptanceModel(replies=replies, metadata={MODEL_LIMITS_METADATA_KEY: {
        'context_window_tokens': 128000, 'max_output_tokens': 1024},
        COMPACTION_RESERVE_METADATA_KEY: {'tokens': 2000},
        COMPACTION_KEEP_RECENT_METADATA_KEY: {'tokens': 20000}})
    monkeypatch.setattr(shared, 'load_model_catalog', lambda _: ModelCatalog('fixture', {'fixture': model}))
    monkeypatch.setattr(shared, 'guard_catalog', lambda *_: None)
    return CaseEnvironment('unused', 'fixture', tmp_path, RequestBudget(10)), model


def test_direct_both_review_stages_validate_real_parser(monkeypatch, tmp_path):
    env, model = prepare(monkeypatch, tmp_path, [
        AIMessage(content='{"decision":"allow","reason":"Requested harmless command"}',
                  response_metadata={'finish_reason': 'stop'}) for _ in range(2)])
    result = asyncio.run(agents.review_roles(env))
    assert result['fast'] == result['detailed'] == 'allow' and len(model.seen) == 2


def test_subagent_read_is_in_child_checkpoint_and_artifact_only_in_root(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, 'nonce', lambda: 'CHILD_VALUE')
    env, model = prepare(monkeypatch, tmp_path, [
        call('subagent', {'agent': 'tester', 'task': 'Read seed.txt and return exact contents'}, 'outer'),
        call('read', {'path': 'seed.txt'}, 'inner'), AIMessage(content='CHILD_VALUE'), AIMessage(content='CHILD_VALUE')])
    result = asyncio.run(agents.child_normal(env))
    assert result['child_read'] and result['root_artifact'] and len(model.seen) == 4


def test_context_control_plan_cannot_exceed_its_allocated_batch():
    plan = context.context_plan()
    assert plan[0][0:2] == ('100E', 10)
    assert sum(case[1] for case in plan[0][2]) <= 10


def test_sibling_approvals_and_child_stop_through_formal_app(monkeypatch, tmp_path):
    left, right = str(tmp_path / 'left.txt'), str(tmp_path / 'right.txt')
    root = AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': left}, 'id': 'outer-a'},
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': right}, 'id': 'outer-b'}])
    env, model = prepare(monkeypatch, tmp_path, [root,
        call('read', {'path': left}, 'inner-a'), call('read', {'path': right}, 'inner-b'),
        AIMessage(content='LEFT_OK'), AIMessage(content='RIGHT_OK'), AIMessage(content='SIBLINGS_OK')])
    assert asyncio.run(agents.siblings(env))['completed_children'] == 2
    assert len(model.seen) == 6


def test_stop_waiting_child_records_stopped_checkpoint_without_success_artifact(monkeypatch, tmp_path):
    env, model = prepare(monkeypatch, tmp_path, [
        call('subagent', {'agent': 'tester', 'task': str(tmp_path / 'outside.txt')}, 'outer'),
        call('read', {'path': str(tmp_path / 'outside.txt')}, 'inner'), AIMessage(content='CHILD_STOPPED')])
    assert asyncio.run(agents.child_stop(env))['parent_continues']
    assert len(model.seen) == 3


def test_skill_case_reads_actual_skill_and_checks_random_instruction(monkeypatch, tmp_path):
    monkeypatch.setattr(context, 'nonce', lambda: 'SKILL_VALUE')
    env, model = prepare(monkeypatch, tmp_path, [call('read', {'path': str(tmp_path / 'work/.agents/skills/acceptance-skill/SKILL.md')}),
                                               AIMessage(content='SKILL_VALUE')])
    assert asyncio.run(context.skills(env))['explicit_command']


def test_manual_summary_and_reopen_do_not_replay_old_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(context, 'nonce', lambda: 'MEMORY_VALUE')
    env, model = prepare(monkeypatch, tmp_path, [AIMessage(content=value, response_metadata={'finish_reason': 'stop'})
        for value in ('ACK', 'RECENT', 'Preserve synthetic identifier MEMORY_VALUE.', 'MEMORY_VALUE')])
    assert asyncio.run(context.compact_reopen(env))['reopen_and_continue']
    assert len(model.seen) == 4


def test_steer_follow_and_active_stop_use_real_event_boundaries(monkeypatch, tmp_path):
    env, model = prepare(monkeypatch, tmp_path, [AIMessage(content=value) for value in ('START_OK', 'STEER_OK', 'FOLLOW_OK')])
    assert asyncio.run(context.controls(env))['followup_delivered']
    assert len(model.seen) == 3


def test_stop_after_first_delta_releases_app_owner(monkeypatch, tmp_path):
    env, model = prepare(monkeypatch, tmp_path, [AIMessage(content='streaming synthetic result')])
    assert asyncio.run(context.stop_stream(env))['active_stop']


def test_auto_pipeline_excludes_prior_file_result_and_keeps_human_and_agents(monkeypatch, tmp_path):
    env, model = prepare(monkeypatch, tmp_path, [call('read', {'path': 'seed.txt'}, 'seed'),
        call('bash', {'command': 'printf AUTO_OK'}, 'shell'),
        AIMessage(content='{"decision":"allow","reason":"Exact harmless command requested"}',
                  response_metadata={'finish_reason': 'stop'}), AIMessage(content='AUTO_OK')])
    result = asyncio.run(agents.auto_pipeline(env))
    assert result['input_isolation'] and result['project_instructions']
    assert result['reviews'] == [{'stage': 'fast', 'decision': 'allow', 'reason': 'valid'}]


def test_auto_unapproved_command_stops_before_review_or_host_execution(monkeypatch, tmp_path):
    from scripts.provider_acceptance_scope import FixtureBoundaryStop
    env, model = prepare(monkeypatch, tmp_path, [call('read', {'path': 'seed.txt'}, 'seed'),
        call('bash', {'command': 'printf UNAUTHORIZED'}, 'shell'), AIMessage(content='must not be consumed')])
    with pytest.raises(FixtureBoundaryStop):
        asyncio.run(agents.auto_pipeline(env))
    assert len(model.seen) == 2
