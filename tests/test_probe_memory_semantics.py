"""Offline checks for the acceptance probe; no configured provider is loaded."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest


def test_fixed_cases_keep_frozen_labels_and_seven_kinds():
    from scripts.probe_memory_semantics import load_cases
    cases, checksum = load_cases()
    assert len(cases) == 12 and len(checksum) == 64
    assert {c['expected']['kind'] for c in cases} == {
        'user', 'rule', 'decision', 'context', 'procedure', 'experience', 'reference', None}


def test_modified_fixture_is_rejected_before_any_model_request(tmp_path):
    from scripts.probe_memory_semantics import load_cases, FIXTURES
    altered = tmp_path / 'fixtures.json'
    altered.write_bytes(FIXTURES.read_bytes() + b' ')
    with pytest.raises(ValueError, match='fixture_hash'):
        load_cases(altered)


def test_saved_sources_have_real_human_tool_reply_and_controlled_time(tmp_path):
    from scripts.probe_memory_semantics import load_cases, seed_sources
    from naiyue_test_agent.application.session_storage import SessionStorage
    from naiyue_test_agent.application.memory_batches import build_batch
    with SessionStorage(tmp_path / 'sessions.sqlite') as store:
        store.bind_workspace(str(uuid4()), tmp_path)
        store.create('s', dict(thread_id='s', model_id='A', workspace_root=tmp_path,
            active_tool_names=['memory'], permission_mode='Default'))
        cases, _ = load_cases()
        facts = seed_sources(store, 's', cases[3]['sources'], at=1000.)
        batch = build_batch(store, session_id='s', thread_id='s', conversation_revision=0,
            start=(0, 0), upper=facts['upper'], token_budget=16000, source_chars=4000)
        sources = {r['message_id']: r for r in batch['sources']}
        assert set(sources) == {'e04-u', 'e04-t'}
        assert sources['e04-u']['role'] == 'human'
        assert sources['e04-t']['tool_call_id'] == 'e04-call'
        assert sources['e04-t']['tool_status'] == 'success'
        assert sources['e04-t']['reply_id'] == sources['e04-u']['reply_id']
        owner = store._read('SELECT reply_id FROM naiyue_chat_messages WHERE message_id=?', ('e04-t-owner',))[0]
        assert owner['reply_id'] == sources['e04-t']['reply_id']
        assert sources['e04-u']['occurred_at'] == 1000.
        assert sources['e04-t']['occurred_at'] > 1000.
        row = store._read('SELECT body_json FROM naiyue_chat_messages WHERE message_id=?', ('e04-u',))[0]
        assert json.loads(row['body_json'])['data']['additional_kwargs']['naiyue_human_input'] is True
        assert store._read('SELECT * FROM naiyue_reply_boundaries')
        assert 'expected' not in json.dumps(batch)


def test_candidate_setup_is_finite_explicit_and_keeps_real_id_version(tmp_path):
    from scripts.probe_memory_semantics import seed_candidate, load_cases
    from naiyue_test_agent.application.session_storage import SessionStorage
    from naiyue_test_agent.application.memory_access import MemoryAccess
    from naiyue_test_agent.memory_settings import MemorySettings
    with SessionStorage(tmp_path / 'sessions.sqlite') as store:
        store.bind_workspace(str(uuid4()), tmp_path)
        store.create('s', dict(thread_id='s', model_id='A', workspace_root=tmp_path,
            active_tool_names=['memory'], permission_mode='Default'))
        access = MemoryAccess(store, MemorySettings(auto_enabled=False), tmp_path / 'data')
        try:
            cases, _ = load_cases()
            setup = seed_candidate(access, 's', cases, at=1000.)
            assert len(setup['actual_memory_id']) == 32 and setup['version'] == 2
            assert setup['fixture_alias'] == 'candidate-e11'
            row = store._read('SELECT * FROM naiyue_memories')[0]
            assert row['status'] == 'candidate' and row['kind'] == 'procedure'
            assert row['scope'] == 'workspace'
            assert json.loads(row['gaps_json']) == ['缺持久选择']
            assert 'asset' not in row['body']  # Reuse original Chinese e05 wording.
            assert row['body'] == cases[4]['sources'][0]['text']
            refs = store._read('SELECT * FROM naiyue_memory_sources WHERE memory_version=2')
            assert {r['role'] for r in refs} == {'ai'}
        finally:
            access.close()


def test_structure_checks_do_not_claim_semantic_forbidden_items_are_proved():
    from scripts.probe_memory_semantics import check_case, load_cases
    case = load_cases()[0][0]
    row = dict(memory_id='a' * 32, version=1, kind='user', status='active', scope='personal',
        body='持久讲解偏好', sources=[dict(message_id='e01-u', quote=case['sources'][0]['text'])])
    value = check_case(case, [row], baseline={})
    assert value['structural_status'] == 'pass'
    assert value['semantic_status'] == 'needs_human_review'
    assert value['forbidden_checks'][0]['status'] == 'needs_human_review'
    invalid = deepcopy(row)
    invalid['sources'][0]['quote'] = '捏造引文'
    assert check_case(case, [invalid], baseline={})['structural_status'] == 'fail'
    assert check_case(case, [], baseline={})['structural_status'] == 'fail'


@pytest.mark.parametrize('index', [7, 8, 9, 11])
def test_negative_cases_never_get_a_false_structural_pass_for_active_rows(index):
    from scripts.probe_memory_semantics import check_case, load_cases
    case = load_cases()[0][index]
    assert check_case(case, [], baseline={})['structural_status'] == 'pass'
    row = dict(memory_id='a' * 32, version=1, kind='context', status='active', scope='workspace', sources=[])
    assert check_case(case, [row], baseline={})['structural_status'] == 'fail'


def test_logical_budget_stops_before_25_and_does_not_change_sdk_retries():
    from scripts.probe_memory_semantics import probe_callback
    from scripts.provider_acceptance_budget import RequestBudget, AcceptanceBudgetStop
    from naiyue_test_agent.memory_settings import MemorySettings
    callback = probe_callback(RequestBudget(24), MemorySettings())
    assert callback.raise_error is True and callback.run_inline is True
    async def invoke():
        for _ in range(24):
            await callback.on_chat_model_start({}, [], run_id=uuid4(), metadata={'naiyue_model_id': 'A'})
        with pytest.raises(AcceptanceBudgetStop):
            await callback.on_chat_model_start({}, [], run_id=uuid4(), metadata={'naiyue_model_id': 'A'})
    asyncio.run(invoke())
    assert callback.budget.snapshot()['used'] == 24


def test_budget_rejection_precedes_real_langchain_generation_without_an_http_request():
    from scripts.probe_memory_semantics import probe_callback
    from scripts.provider_acceptance_budget import RequestBudget, AcceptanceBudgetStop
    from naiyue_test_agent.memory_settings import MemorySettings
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    model = FakeMessagesListChatModel(responses=[AIMessage(content='first'), AIMessage(content='second')],
        metadata={'naiyue_model_id': 'A'}, callbacks=[probe_callback(RequestBudget(1), MemorySettings())])
    async def invoke():
        await model.ainvoke('synthetic')
        assert model.i == 1
        with pytest.raises(AcceptanceBudgetStop):
            await model.ainvoke('synthetic')
        assert model.i == 1
    asyncio.run(invoke())


def test_report_redacts_credentials_without_removing_safe_synthetic_assertions():
    from scripts.probe_memory_semantics import safe_report
    value = safe_report(dict(body='Authorization: Bearer SYNTHETIC_NOT_A_CREDENTIAL',
        ordinary='以后测试使用.venv', marker='SYNTHETIC_NOT_A_CREDENTIAL'))
    assert value['body'] == '[redacted_sensitive_text]'
    assert value['ordinary'] == '以后测试使用.venv'
    assert value['marker'] == 'SYNTHETIC_NOT_A_CREDENTIAL'


def test_cli_refuses_more_than_two_repeats_and_requires_explicit_config(tmp_path):
    from scripts.probe_memory_semantics import arguments
    with pytest.raises(SystemExit):
        arguments(['--output', str(tmp_path / 'output.json')])
    with pytest.raises(SystemExit):
        arguments(['--config', 'models.json', '--output', str(tmp_path / 'output.json'), '--repeat', '3'])


def test_probe_reaches_production_worker_with_an_offline_model():
    from scripts.probe_memory_semantics import run_case,load_cases,probe_callback
    from scripts.provider_acceptance_budget import RequestBudget
    from naiyue_test_agent.memory_settings import MemorySettings
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback
    from test_async_application import StreamingToolModel
    from test_memory_jobs import response
    settings=MemorySettings(auto_enabled=False)
    observer=probe_callback(RequestBudget(24),settings)
    model=StreamingToolModel(replies=[response()],callbacks=[observer],metadata={'naiyue_model_id':'A'})
    catalog=ModelCatalog('A',{'A':model});install_model_usage_callback(catalog)
    result=asyncio.run(run_case(load_cases()[0][0],1,catalog,'A',observer,settings))
    assert result['task']['result_status']=='completed' and result['invocation_pairing']
    assert result['overview']['overview_status']=='generated'
    assert observer.budget.snapshot()['used']==1 and len(model.seen)==1
    assert result['events'][0]['data']['mode']=='memory'
    assert len(result['model_inputs'])==1
    assert [message['type'] for message in result['model_inputs'][0]]==['system','human']
    sent=json.loads(result['model_inputs'][0][1]['data']['content'])
    assert sent['batch']['sources'] and all('source_key' in s for s in sent['batch']['sources'])


@pytest.mark.parametrize('mode', ['text', 'json_schema', 'function_calling'])
def test_probe_reports_the_explicit_model_output_protocol(mode):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from scripts.probe_memory_semantics import probe_callback
    from scripts.provider_acceptance_budget import RequestBudget
    from naiyue_test_agent.memory_settings import MemorySettings
    value = dict(overview=dict(goal='目标', progress='进展', blockers='', next_step='继续'), proposals=[])
    response = AIMessage(content=json.dumps(value, ensure_ascii=False))
    if mode == 'function_calling':
        response = AIMessage(content='', tool_calls=[dict(name='MemoryResult', args=value, id='return-1')])
    observer = probe_callback(RequestBudget(1), MemorySettings(), output_mode=mode)
    model = FakeMessagesListChatModel(responses=[response], callbacks=[observer],
        metadata={'naiyue_model_id': 'A'})
    asyncio.run(model.ainvoke('synthetic'))
    assert observer.outputs == [dict(parsed=value)]
    assert observer.budget.snapshot()['used'] == 1 and len(observer.inputs) == 1
