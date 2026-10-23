"""Behavior contracts for offline lexical experiments, not product memory."""

import json
from pathlib import Path

import pytest

from docs.research.memory00 import retrieval_tuning as tuning
from docs.research.memory00 import benchmark_tuning as benchmark


def row(identity, *, title='', body='', score=0.01, scope='workspace'):
    return {'id': identity, 'title': title, 'aliases': '', 'body': body,
            'scope': scope, 'score': score, 'version': 1}


def test_cascade_uses_complete_expression_and_does_not_fill_with_weak_results():
    candidates = [row('environment', body='测试执行前确认解释器', score=0.02),
                  row('class', title='pytest测试类组织', score=0.01)]
    actual = tuning.select_results(candidates, '测试类', route='cascade_rrf', coverage=(1, 2))
    assert [r['id'] for r in actual] == ['class']


def test_coverage_fallback_keeps_reordered_expression_but_not_single_generic_word():
    candidates = [row('generic', title='用例编号'), row('workflow', title='生成用例')]
    actual = tuning.select_results(candidates, '用例生成', route='cascade_rrf', coverage=(2, 3))
    assert [r['id'] for r in actual] == ['workflow']


def test_coverage_threshold_is_a_real_parameter():
    candidates = [row('partial', title='测试环境'), row('complete', title='测试类')]
    loose = tuning.select_results(candidates, '测试类', route='coverage_rrf', coverage=(1, 2))
    strict = tuning.select_results(candidates, '测试类', route='coverage_rrf', coverage=(2, 3))
    assert [r['id'] for r in loose] == ['partial', 'complete']
    assert [r['id'] for r in strict] == ['complete']


def test_quality_merge_uses_match_strength_instead_of_each_corpus_first_place():
    candidates = [row('personal', body='测试类的资料位置已记录', score=0.02, scope='personal'),
                  row('project', title='测试类组织', score=0.01)]
    actual = tuning.select_results(candidates, '测试类', route='cascade_quality', coverage=(2, 3))
    assert [r['id'] for r in actual] == ['project', 'personal']


def test_all_weak_candidates_can_be_rejected_without_filling_result_budget():
    assert tuning.select_results([row('noise', title='测试环境')], '测试类',
                                 route='cascade_rrf', coverage=(3, 4)) == []


def test_metrics_report_micro_precision_and_count_positive_empty_as_failure():
    rows = [dict(actual_ids=['right', 'wrong'], relevant_ids=['right'], forbidden_hits=[]),
            dict(actual_ids=[], relevant_ids=['missed'], forbidden_hits=[]),
            dict(actual_ids=['noise'], relevant_ids=[], forbidden_hits=[], no_answer=True)]
    metrics = benchmark.summarize(rows)
    assert metrics['micro_precision'] == pytest.approx(1 / 3)
    assert metrics['macro_recall'] == 0.5
    assert metrics['positive_macro_f0_5'] == pytest.approx(5 / 18)
    assert metrics['hit_at_1'] == 0.5
    assert metrics['mrr_at_8'] == 0.5
    assert metrics['no_answer_errors'] == 1


def test_fixture_groups_are_disjoint_and_input_sizes_are_fixed():
    data = json.loads((Path(__file__).parent / 'tuning-fixtures.json').read_text())
    assert len(data['queries']) == 144
    assert len(data['memories']) == 271
    groups = {split: {q['group'] for q in data['queries'] if q['split'] == split}
              for split in ('calibration', 'validation', 'test')}
    assert len(groups['calibration']) == 18
    assert len(groups['validation']) == len(groups['test']) == 9
    assert not groups['calibration'] & groups['validation']
    assert not groups['calibration'] & groups['test']
    assert not groups['validation'] & groups['test']
    assert all(data['group_split'][q['group']] == q['split'] for q in data['queries'])


def test_parameter_selection_does_not_use_validation_or_test_labels(monkeypatch):
    data = {'queries': [{'split': split} for split in ('calibration', 'validation', 'test')]}
    seen = []

    def run(data, route, config, protocol, split):
        seen.append(split)
        return [dict(actual_ids=['yes'], relevant_ids=['yes'], forbidden_hits=[])]

    monkeypatch.setattr(benchmark, 'run_queries', run)
    result = benchmark.calibrate(data, {'routes': ['substring'], 'weights': [[1, 1, 1]],
                                        'coverages': [[1, 2]]})
    assert seen == ['calibration']
    assert result['substring']['weights'] == [1, 1, 1]


def test_final_test_does_not_replace_frozen_choice_with_test_winner(monkeypatch):
    def run(data, route, config, protocol, split):
        actual = [] if route == 'substring' else ['right']
        return [dict(actual_ids=actual, relevant_ids=['right'], forbidden_hits=[])]

    monkeypatch.setattr(benchmark, 'run_queries', run)
    config = {'weights': [1, 1, 1], 'coverage': [1, 2]}
    frozen = {'profiles': {'substring': config, 'adapted_or': config},
              'validation': {'selected_route': 'substring'}}
    actual = benchmark.evaluate({}, {}, frozen)
    assert actual['selected_route'] == 'substring'
    assert actual['test_routes']['substring']['summary']['macro_recall'] == 0
    assert actual['test_routes']['adapted_or']['summary']['macro_recall'] == 1


@pytest.mark.parametrize('route', ['substring', 'adapted_or', 'coverage_rrf',
                                  'cascade_rrf', 'cascade_quality'])
def test_common_engine_filters_survive_experiment_wrapper(route):
    data = {'as_of': '2026-10-22T12:00:00Z', 'memories': [
        {'id': 'wrong', 'scope': 'workspace', 'workspace_id': 'ws-b', 'kind': 'rule',
         'status': 'active', 'title': 'NO_PROXY', 'aliases': [], 'body': ''},
        {'id': 'near', 'scope': 'workspace', 'workspace_id': 'ws-a', 'kind': 'rule',
         'status': 'active', 'title': 'NO_PROXY_EXTRA', 'aliases': [], 'body': ''}],
        'queries': [{'id': 'q', 'split': 'test', 'query': 'NO_PROXY',
                     'context': {'workspace_id': 'ws-a'}, 'relevant_ids': [], 'required_ids': []}]}
    protocol = {'rrf_k': 60, 'candidate_limit': 50, 'result_limit': 8}
    actual = benchmark.run_queries(data, route, {'weights': [3, 2, 1], 'coverage': [2, 3]}, protocol, 'test')
    assert actual[0]['actual_ids'] == []
    assert actual[0]['forbidden_hits'] == []


@pytest.mark.parametrize('route', ['coverage_rrf', 'cascade_rrf', 'cascade_quality'])
def test_explicit_concept_expansion_remains_valid_after_candidate_selection(route):
    data = {'as_of': '2026-10-22T12:00:00Z', 'memories': [
        {'id': 'environment', 'scope': 'workspace', 'workspace_id': 'ws-a', 'kind': 'context',
         'status': 'active', 'title': 'venv', 'aliases': [], 'body': ''}],
        'queries': [{'id': 'q', 'split': 'test', 'query': '虚拟环境', 'expansions': ['venv'],
                     'context': {'workspace_id': 'ws-a'}, 'relevant_ids': ['environment'], 'required_ids': []}]}
    protocol = {'rrf_k': 60, 'candidate_limit': 50, 'result_limit': 8}
    actual = benchmark.run_queries(data, route, {'weights': [3, 2, 1], 'coverage': [2, 3]}, protocol, 'test')
    assert actual[0]['actual_ids'] == ['environment']


def test_validation_choice_rejects_a_known_required_regression(monkeypatch):
    def run(data, route, config, protocol, split):
        actual = [] if route == 'coverage_rrf' and split != 'validation' else ['right']
        return [dict(actual_ids=actual, relevant_ids=['right'], required_ids=['right'], forbidden_hits=[])]

    monkeypatch.setattr(benchmark, 'run_queries', run)
    config = {'weights': [1, 1, 1], 'coverage': [3, 4]}
    profiles = {'coverage_rrf': config, 'cascade_rrf': config}
    actual = benchmark.validate({}, {'routes': list(profiles)}, profiles)
    assert actual['selected_route'] == 'cascade_rrf'
    assert actual['regressions']['coverage_rrf']['passes'] is False


def test_invalid_expansion_anchor_cannot_supply_postfilter_coverage():
    candidate = row('only_original', title='项目规则', body='测试 TC-01')
    actual = tuning.select_results([candidate], '测试规范', route='cascade_rrf',
                                   coverage=(3, 4), expansions=['项目规则 TC-02'])
    assert actual == []
