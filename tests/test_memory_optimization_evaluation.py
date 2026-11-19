"""Engineering evaluator does not mistake empty results or changed data for success."""
import json
import pytest
from scripts.memory_evaluation_cases import write_report,load_cases
from scripts.evaluate_memory_optimization import evidence_evaluate


def test_report_cannot_overwrite_or_follow_symlink(tmp_path):
    p=tmp_path/'report.json'
    write_report(p,{'score':None})
    with pytest.raises(FileExistsError):write_report(p,{})
    link=tmp_path/'link';link.symlink_to(tmp_path/'absent')
    with pytest.raises(FileExistsError):write_report(link,{})
    assert json.loads(p.read_text())=={'score':None}


def test_real_evidence_entrypoint_and_explicit_pending_stages():
    result=evidence_evaluate()
    assert result['provider_calls']==0
    assert all(c['passed'] for c in result['cases'])
    assert result['semantic_status']=='semantic_pending'
    assert result['S3']==result['S4']=='pending'
    cases,checksum=load_cases()
    assert checksum==result['fixtures_sha256'] and len(cases)==len(result['cases'])


def test_full_suite_is_balanced_and_family_isolated():
    from scripts.memory_evaluation_cases import load_suite
    cases,protocol=load_suite()
    assert len(cases)==96
    families={}
    for case in cases:
        assert families.setdefault(case['family_id'],case['split'])==case['split']
        assert case['expected_facts'] and case['sources']
    for suite in ('input','comparison','recall','semantic'):
        for split in ('calibration','validation','holdout'):
            assert sum(c['suite']==suite and c['split']==split for c in cases)==8


def test_empty_baseline_cannot_pass_and_changed_protocol_is_rejected():
    from scripts.evaluate_memory_optimization import compare_reports
    with pytest.raises(ValueError,match='baseline'):
        compare_reports({'fixtures_sha256':'x'}, {})


def test_semantic_preflight_rejects_excess_before_loading_config(tmp_path):
    from scripts.probe_memory_semantics import arguments,selected_cases
    args=arguments(['--config','absent.json','--output',str(tmp_path/'report.json'),
        '--suite','optimization','--split','calibration','--repeat','2'])
    with pytest.raises(ValueError,match='budget'):selected_cases(args,calls_per_case=2)


def test_case_regression_cannot_hide_behind_equal_total_passes():
    from scripts.evaluate_memory_optimization import compare_reports
    common=dict(fixtures_sha256='f',protocol_sha256='p',split='calibration',
        manifest={'source_sha256':{'a':'b'}})
    baseline=dict(common,cases=[{'case_id':'a','passed':True},{'case_id':'b','passed':False}])
    current=dict(common,cases=[{'case_id':'a','passed':False},{'case_id':'b','passed':True}])
    assert compare_reports(current,baseline)['regressions']==['a']


def test_offline_wiki_manifest_does_not_claim_execution(tmp_path):
    from scripts.memory_wiki_evaluation import main

    main(['--mode', 'offline', '--split', 'calibration', '--output-dir', str(tmp_path)])
    report = json.loads((tmp_path/'manifest.json').read_text())
    assert len(report['case_ids']) == 8 and report['model_requests'] == 0
    assert report['model_id'] is None and report['product_quality_passed'] is None
    assert report['status'] == 'contracts_validated' and not (tmp_path/'report.json').exists()


def test_fixture_hash_mismatch_is_rejected_before_execution(tmp_path):
    from scripts.memory_evaluation_cases import FIXTURES,load_suite
    fixture=tmp_path/'fixtures.json'
    fixture.write_bytes(FIXTURES.with_name('fixtures.json').read_bytes()+b' ')
    with pytest.raises(ValueError,match='fixture_changed'):load_suite(fixture)


def test_missing_citable_id_fails_input_evaluation(monkeypatch):
    import asyncio
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.application.memory import memory_job_runner
    from naiyue_test_agent.model_access import ModelCatalog
    from scripts.memory_wiki_runtime_evaluation import background_case
    from test_async_application import StreamingToolModel
    from test_memory_batches import result
    from test_memory_foreground_evaluation import wiki_case
    from memory_wiki_helpers import proposed

    original = memory_job_runner.build_batch

    def missing(*args, **kwargs):
        material = original(*args, **kwargs)
        for unit in material['units']:
            if unit.get('evidence_id') == 'E1':
                unit['evidence_id'] = None
        assert material['evidence_map'].pop('E1')
        return material

    monkeypatch.setattr(memory_job_runner, 'build_batch', missing)
    model = StreamingToolModel(replies=[AIMessage(content=json.dumps(result(proposals=[proposed()])))])
    report = asyncio.run(background_case(wiki_case('W04', followup=False), ModelCatalog('A', {'A': model}), 'A'))
    observed = report['observations']
    assert report['status'] == 'failed' and observed['overview_saved'] == observed['failures'] == 1
    assert observed['saved'] == 0 and observed['memories'] == observed['sources'] == []
    assert len(model.seen) == 1 and not model.replies


def test_correct_prohibition_is_not_rejected_as_wrong_claim(tmp_path):
    from scripts.probe_memory_semantics import arguments,selected_cases,check_case
    args=arguments(['--config','absent','--output',str(tmp_path/'report'),
        '--suite','optimization','--repeat','1'])
    case=next(c for c in selected_cases(args)[0] if c['id']=='semantic-07')
    text=case['sources'][0]['text']
    row=dict(memory_id='correct',version=1,status='active',kind='context',body=text,
        sources=[dict(message_id='u',quote=text)])
    result=check_case(case,[row],baseline={})
    assert result['structural_status']=='pass'
    assert result['semantic_status']=='needs_human_review'
    assert case['expected']['forbidden_claim']=='正式运行启用调试参数。'


def test_full_suite_rejects_changed_rubric(tmp_path):
    from scripts.memory_evaluation_cases import FIXTURES,load_suite
    for name in ('fixtures.json','protocol.json'):
        (tmp_path/name).write_bytes(FIXTURES.with_name(name).read_bytes())
    (tmp_path/'rubric.md').write_text('changed rules')
    with pytest.raises(ValueError,match='rubric_changed'):
        load_suite(tmp_path/'fixtures.json',tmp_path/'protocol.json')
