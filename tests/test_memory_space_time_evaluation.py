"""S5 frozen labels reject unsafe or ambiguous evaluation contracts."""
import copy
import hashlib
import json

import pytest
from scripts.memory_evaluation_cases import load_suite

ROOT = __import__('pathlib').Path(__file__).parent / 'data/memory_space_time'


def load():
    return load_suite(ROOT / 'fixtures.json', ROOT / 'protocol.json')


def test_two_dimension_suite_is_balanced_and_explicit():
    cases, protocol = load()
    assert protocol['format'] == 2
    assert len(cases) == 24
    assert len({c['family_id'] for c in cases}) == 24
    for split in ('calibration', 'validation', 'holdout'):
        assert sum(c['split'] == split for c in cases) == 8
    assert any(c['old_memories'] for c in cases)
    assert all(c['followup_query'] and c['answer_requirements'] for c in cases)


@pytest.mark.parametrize('change', ['scope', 'time', 'offset', 'flags', 'bool', 'seed', 'family'])
def test_invalid_v2_labels_fail_before_execution(tmp_path, change):
    cases, protocol = load()
    data = dict(format=2, synthetic=True, cases=copy.deepcopy(cases))
    c = data['cases'][0]; fact = c['expected_facts'][0]
    if change == 'scope': fact['allowed_scopes'] = ['both']
    if change == 'time': fact['allowed_time_kinds'] = ['user']
    if change == 'offset': fact['evidence'][0]['start'] = 9999
    if change == 'flags': fact['must_not_activate'] = True
    if change == 'bool': fact['must_extract'] = 1
    if change == 'seed': c['old_memories'] = [dict(alias='unexplained')]
    if change == 'family': data['cases'][8]['family_id'] = c['family_id']
    fixture = tmp_path / 'fixtures.json'; fixture.write_text(json.dumps(data))
    (tmp_path / 'rubric.md').write_bytes((ROOT / 'rubric.md').read_bytes())
    protocol = dict(protocol, suite_fixtures_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest())
    path = tmp_path / 'protocol.json'; path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError): load_suite(fixture, path)


def test_v2_missing_review_dimensions_cannot_pass():
    from scripts.review_memory_evaluation import review
    report = dict(report_schema_version=2, run_id='r', cases=[dict(case_id='a', expected_facts=[
        dict(fact_id='f', must_extract=True, must_activate=True, must_inject=True)])])
    rows = [dict(case_id='a', supported=True, wrong_overwrite=False, unsupported_activation=False,
        negation_reversed=False, scope_expanded=False, reviewer='human', reason='checked',
        facts=[dict(fact_id='f', extracted=True, available=True, injected=True, reason='checked')])]
    with pytest.raises(ValueError, match='review_verdict_missing'): review(report, rows)
    rows[0].update(wrong_long_term=False, answer_passed=True, scope_correct=True, time_correct=True)
    assert review(report, rows)['semantic_safety_passed'] is True
    rows[0]['wrong_long_term'] = True
    assert review(report, rows)['semantic_safety_passed'] is False


def test_space_time_preflight_selects_eight_and_reserves_answer_call(tmp_path):
    from scripts.probe_memory_semantics import arguments, selected_cases
    args = arguments(['--config','absent','--output',str(tmp_path/'out'), '--suite','space-time'])
    cases, checksum = selected_cases(args)
    assert len(cases) == 8 and checksum
    assert all(c['space_time'] and c['expected']['status']=='fact_review' for c in cases)
    args.repeat = 2
    with pytest.raises(ValueError, match='budget'): selected_cases(args)


def _background_with_followup(answer, *extra):
    import asyncio
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.model_access import ModelCatalog
    from scripts.memory_wiki_runtime_evaluation import background_case
    from test_memory_foreground_evaluation import wiki_case
    from test_memory_batches import result
    from test_async_application import StreamingToolModel
    from test_memory_wiki_worker import finish, read
    from memory_wiki_helpers import proposed

    case = wiki_case('W04')
    model = StreamingToolModel(replies=[
        AIMessage(content=json.dumps(result(proposals=[proposed(body='先运行相关回归，再汇报完成。')]))),
        read(dict(action='list_pages')),
        finish(decisions=[dict(relation='add', proposal_ids=['P1'], destination='N1')],
               page_changes=[dict(ref='N1', topic='working_agreements', title='回归条件', description='')]),
        answer, *extra,
    ])
    return asyncio.run(background_case(case, ModelCatalog('A', {'A': model}), 'A')), model


def test_background_followup_uses_saved_memory_and_real_calls():
    from langchain_core.messages import AIMessage
    from test_async_application import call

    outcome, model = _background_with_followup(
        call('memory', {'request': dict(action='search', scope='workspace', query='相关回归')}),
        AIMessage(content='先跑相关回归。'))
    assert outcome['status'] == 'completed' and len(model.seen) == 5 and not model.replies
    observed = outcome['observations']
    assert observed['saved'] == observed['overview_saved'] == 1
    followup = observed['followup']
    assert followup['memory_unchanged'] and followup['logical_calls'] == 2
    assert followup['answer'] == '先跑相关回归。'
    proof, = followup['tool_feedback']
    assert json.loads(proof['content'])['items'][0]['memory_id'] == observed['memories'][0]['memory_id']


def test_baseline_time_classification_is_not_applicable():
    from scripts.review_memory_evaluation import review
    report = dict(report_schema_version=2, policy='baseline', run_id='r', cases=[dict(case_id='a')])
    row = dict(case_id='a', supported=True, wrong_overwrite=False, unsupported_activation=False,
        negation_reversed=False, scope_expanded=False, wrong_long_term=False, answer_passed=True,
        scope_correct=True, time_correct=None, reviewer='human', reason='旧链没有两类字段')
    result = review(report, [row])
    assert result['dimension_metrics']['time_correct']['rate'] is None
    assert result['dimension_metrics']['scope_correct']['rate'] == 1


@pytest.mark.parametrize('case_id', ['W07', 'W17'])
def test_old_seed_setup_is_explicit_and_source_visibility_is_preserved(tmp_path, case_id):
    import time
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    from naiyue_test_agent.memory_settings import MemorySettings
    from scripts.memory_wiki_runtime_evaluation import seed_wiki
    from test_memory_foreground_evaluation import wiki_case
    from test_memory_wiki_pagination import decode_content

    case = wiki_case(case_id, followup=False)['input']
    project = '11111111-1111-4111-8111-111111111111'
    with ProjectStore(tmp_path/'sessions.sqlite') as store:
        store.sessions.bind_workspace(project, tmp_path)
        store.sessions.create('probe', dict(thread_id='probe', model_id='A', workspace_root=tmp_path,
            active_tool_names=['memory'], permission_mode='Default'))
        store.memory.prepare_wiki(now=time.time())
        memory = MemoryAccess(store, MemorySettings(), tmp_path/'data')
        try:
            pages = seed_wiki(memory, 'probe', case, time.time())
            assert len(pages) == 1 and 'P1' in pages
            row = store.database.connection.execute('SELECT memory_id,body FROM naiyue_memories').fetchone()
            read = memory.query_wiki('probe', project, dict(action='read', scope='workspace',
                memory_id=row['memory_id']))
            entry, = decode_content(read)['entries']
            proof, = entry['sources']
            assert entry['body'] == row['body'] == case['wiki']['memories'][0]['body']
            assert proof['available'] is case['scenario']['source_available']
            assert ('quote' in proof) is proof['available']
        finally:
            memory.close()


def test_failed_model_json_retains_only_visible_diagnostic_text():
    import asyncio
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import LLMResult, ChatGeneration
    from scripts.probe_memory_semantics import probe_callback
    from scripts.provider_acceptance_budget import RequestBudget
    from naiyue_test_agent.memory_settings import MemorySettings
    callback=probe_callback(RequestBudget(3),MemorySettings())
    message=AIMessage(content=[dict(type='reasoning',encrypted_content='PRIVATE-BLOCK'),
        dict(type='text',text='{"groups": [broken-json')])
    asyncio.run(callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]),run_id='bad'))
    row=callback.outputs[0]
    assert row['raw_text']=='{"groups": [broken-json'
    assert row['stage']=='extract' and row['error_code']=='memory_result_invalid'
    assert 'PRIVATE-BLOCK' not in json.dumps(row)


def test_partial_v2_report_remains_reviewable_but_cannot_pass():
    from scripts.review_memory_evaluation import review
    report=dict(report_schema_version=2,policy='baseline',run_id='r',expected_cases=1,
        status='incomplete_or_structural_failure',structural_failures=1,
        cases=[dict(case_id='a')],failures=[])
    row=dict(case_id='a',supported=True,wrong_overwrite=False,unsupported_activation=False,
        negation_reversed=False,scope_expanded=False,wrong_long_term=False,answer_passed=False,
        scope_correct=True,time_correct=None,reviewer='human',reason='partial case examined')
    result=review(report,[row])
    assert result['dimension_metrics']['answer_passed']['total']==1
    assert result['engineering_passed'] is False
    assert result['overall']=='incomplete_or_structural_failure'


@pytest.mark.parametrize('failure', ['timeout', 'tool'])
def test_followup_failure_preserves_completed_background_material(failure):
    from langchain_core.messages import AIMessage
    from test_async_application import call

    if failure == 'timeout':
        result, model = _background_with_followup(TimeoutError('PRIVATE FAILURE BODY'))
    else:
        result, model = _background_with_followup(
            call('memory', {'request': dict(action='refresh_overview')}),
            AIMessage(content='操作未获准。'))
    assert result['status'] == 'failed'
    observed = result['observations']
    assert observed['result_status'] == 'completed' and observed['saved'] == 1
    assert len(observed['memories']) == 1 and observed['memories'][0]['status'] == 'active'
    assert observed['followup']['status'] == 'failed'
    assert len(model.seen) == (4 if failure == 'timeout' else 5)
    assert 'PRIVATE FAILURE BODY' not in json.dumps(result)
    assert len(observed['sources']) == 1
    if failure == 'tool':
        assert observed['followup']['tool_feedback'][0]['status'] == 'error'
        assert observed['followup']['memory_unchanged']


def test_probe_source_snapshot_includes_explicit_old_seed_text():
    import asyncio
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.model_access import ModelCatalog
    from scripts.memory_wiki_runtime_evaluation import background_case
    from test_async_application import StreamingToolModel
    from test_memory_foreground_evaluation import wiki_case
    from test_memory_batches import result

    case = wiki_case('W07', followup=False)
    model = StreamingToolModel(replies=[AIMessage(content=json.dumps(result()))])
    report = asyncio.run(background_case(case, ModelCatalog('A', {'A': model}), 'A'))
    old, = report['observations']['sources']
    assert old['message_id'] == 'old-0' and old['session_id'] == 'old'
    assert old['quote'] == case['input']['wiki']['memories'][0]['body']
