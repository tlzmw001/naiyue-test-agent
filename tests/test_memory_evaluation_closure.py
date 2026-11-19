import json
import pytest


def test_worker_matrix_runs_real_jobs_and_preserves_state():
    from langchain_core.messages import AIMessage
    from test_async_application import call
    from test_memory_space_time_evaluation import _background_with_followup

    outcome, model = _background_with_followup(
        call('memory', {'request': dict(action='search', scope='workspace', query='相关回归')}),
        AIMessage(content='先跑相关回归。'))
    assert outcome['status'] == 'completed' and not model.replies
    observed = outcome['observations']
    assert observed['followup']['memory_unchanged'] and observed['saved'] == 1
    row, = observed['memories']
    proof, = observed['sources']
    assert (proof['memory_id'], proof['memory_version']) == (row['memory_id'], row['version'])
    assert proof['message_id'] == 'new-0' and proof['end_offset'] > proof['start_offset']


def test_review_requires_complete_unique_identity_and_rejects_unsafe_claim():
    from scripts.review_memory_evaluation import review
    report={'run_id':'run1','cases':[{'case_id':'a'}]}
    rows=[dict(case_id='a',reviewer='human',reason='checked source',supported=True,
        wrong_overwrite=False,unsupported_activation=False,negation_reversed=False,scope_expanded=False)]
    assert review(report,rows)['semantic_safety_passed']
    for broken in ([],rows+rows,[dict(rows[0],case_id='unknown')]):
        with pytest.raises(ValueError):review(report,broken)
    assert not review(report,[dict(rows[0],negation_reversed=True)])['semantic_safety_passed']


def test_human_rating_cannot_approve_incomplete_model_run():
    from scripts.review_memory_evaluation import review
    report=dict(run_id='r',status='incomplete_or_structural_failure',expected_cases=2,
        cases=[dict(case_id='a')])
    rows=[dict(case_id='a',reviewer='human',reason='checked',supported=True,
        wrong_overwrite=False,unsupported_activation=False,negation_reversed=False,scope_expanded=False)]
    with pytest.raises(ValueError,match='incomplete'):review(report,rows)


def test_fact_denominators_include_missed_facts_and_forbidden_activation():
    from scripts.review_memory_evaluation import review
    facts=[dict(fact_id='a',must_extract=True,must_activate=True,must_not_activate=False),
        dict(fact_id='b',must_extract=True,must_activate=True,must_not_activate=False),
        dict(fact_id='bad',must_extract=False,must_activate=False,must_not_activate=True)]
    report=dict(run_id='r',cases=[dict(case_id='c',expected_facts=facts)])
    row=dict(case_id='c',reviewer='human',reason='checked',supported=True,wrong_overwrite=False,
        unsupported_activation=False,negation_reversed=False,scope_expanded=False)
    with pytest.raises(ValueError,match='fact'):review(report,[row])
    row['facts']=[dict(fact_id=f['fact_id'],extracted=f['fact_id']=='a',available=f['fact_id']!='b',
        injected=False,reason='checked') for f in facts]
    r=review(report,[row])
    assert r['fact_metrics']['proposal_recall']==0.5
    assert r['fact_metrics']['effective_recall']==0.5
    assert r['fact_metrics']['missed_extraction']==['c:b']
    assert r['fact_metrics']['forbidden_activation']==['c:bad']
    assert not r['semantic_safety_passed']
    row['facts'].append(row['facts'][0])
    with pytest.raises(ValueError,match='fact'):review(report,[row])


def test_navigation_calibration_has_distractors_and_empty_run_does_not_pass_quality():
    from scripts.memory_wiki_evaluation import load_suite
    from test_memory_foreground_evaluation import run_case, wiki_case
    from test_async_application import StreamingToolModel, call
    from langchain_core.messages import AIMessage

    case = next(c for c in load_suite()[0] if c['case_id'] == 'W13')
    assert len(case['input']['wiki']['pages']) == 80 and case['expected']['required_reads']
    model = StreamingToolModel(replies=[
        call('memory', {'request': dict(action='list_pages', scope='workspace', topic='working_agreements')}),
        AIMessage(content='没有该主题。')])
    report = run_case(wiki_case('W13', followup=False), model)
    assert report['semantic_review'] is None
    observed = report['observations']
    assert observed['memories'] == observed['baseline_memories']
    assert json.loads(observed['tool_feedback'][0]['content'])['items'] == []


def test_real_probe_keeps_frozen_fact_labels(tmp_path):
    from scripts.probe_memory_semantics import arguments,selected_cases
    args=arguments(['--config','absent','--output',str(tmp_path/'result'),
        '--suite','optimization','--split','calibration','--repeat','1'])
    cases,_=selected_cases(args)
    assert len(cases)==8 and all(c['expected_facts'] for c in cases)


def test_recall_comparison_cannot_hide_precision_loss_behind_pass_count():
    from scripts.memory_recall_evaluation import compare
    common=dict(fixtures_sha256='f',protocol_sha256='p',split='calibration',manifest={'source_sha256':{'a':'b'}})
    old=dict(common,cases=[dict(case_id='a',passed=True,recall=1.,precision=1.)])
    new=dict(common,cases=[dict(case_id='a',passed=True,recall=1.,precision=.5)])
    assert compare(new,old)['metric_regressions']==['a:precision']
