"""Program contracts and annotation isolation for offline robustness probes."""

import json
from pathlib import Path

import pytest

from docs.research.memory00 import benchmark_tuning as bench
from docs.research.memory00 import retrieval_robustness as robust


def row(identity, title='用例要求', body='用例要求包含断言。', **changes):
    return {'id': identity, 'scope': 'workspace', 'workspace_id': 'ws-a',
            'kind': 'rule', 'status': 'active', 'title': title, 'aliases': '', 'body': body,
            'score': 0.01, 'version': 1, 'machine': None, 'target': None, 'module': None, **changes}


def test_topic_excludes_contrast_mention_without_splitting_decimal_anchor():
    rows = [row('ratio', '小数比例', '比例0.5是有效值；不能拆成0或5。'),
            row('zero', '业务零值', '业务值0是有效值；不能当缺省。')]
    actual = robust.select_results(rows, '0', route='topical_pool')
    assert [r['id'] for r in actual] == ['zero']


def test_no_topic_match_falls_back_to_visible_body_instead_of_losing_record():
    rows = [row('environment', '准备记录', '先读说明；测试选择venv虚拟环境。')]
    actual = robust.select_results(rows, '虚拟环境', route='topical_pool')
    assert [r['id'] for r in actual] == ['environment']


def test_topic_pool_keeps_distinct_partial_facts_when_complete_match_exists():
    rows = [row('structure'), row('names', '用例命名要求', '用例命名要求定义类名。'),
            row('cleanup', '用例清理要求', '用例清理要求定义收尾。')]
    actual = robust.select_results(rows, '用例要求', route='topical_pool')
    assert {r['id'] for r in actual} == {'structure', 'names', 'cleanup'}


def test_exact_duplicates_have_one_representative_and_preserve_member_ids():
    rows = [row('a1'), row('a2'), row('b', body='用例要求包括清理。')]
    actual = robust.select_results(rows, '用例要求', route='topical_exact')
    assert len(actual) == 2
    assert set(actual[0]['duplicate_ids']) == {'a2'}
    assert actual[0]['id'] == 'a1'


@pytest.mark.parametrize('change', [dict(scope='personal'), dict(machine='windows'),
    dict(target='production'), dict(module='billing'), dict(kind='decision'),
    dict(body='用例要求禁止执行。'), dict(body='用例要求允许执行。'),
    dict(body='用例要求使用TC-41。'), dict(body='用例要求使用0.5。')])
def test_similar_but_distinct_records_are_not_hard_merged(change):
    rows = [row('a'), row('b', **change)]
    actual = robust.select_results(rows, '用例要求', route='topical_exact')
    assert len(actual) == 2
    assert all(not r.get('duplicate_ids') for r in actual)


def test_diversity_places_another_fact_before_near_duplicate():
    rows = [row('a', body='用例要求包含前置断言清理。'),
            row('near', body='用例要求包含前置断言清理，仍按此执行。'),
            row('other', '用例权限要求', '用例权限要求先审批；未批准不执行。')]
    actual = robust.select_results(rows, '用例要求', route='topical_diverse', diversity_lambda=0.6)
    assert [r['id'] for r in actual[:2]] == ['a', 'other']


def test_fact_scoring_preserves_raw_record_count_and_does_not_reward_duplicates():
    rows = [dict(actual_ids=['a1', 'a2', 'b'], evaluation_ids=['fact-a', 'fact-a', 'fact-b'],
                 relevant_ids=['fact-a', 'fact-b'], forbidden_hits=[], stratum='duplicates')]
    summary = bench.summarize(rows)
    assert summary['record_precision'] == 1
    assert summary['micro_precision'] == pytest.approx(2 / 3)
    assert summary['macro_recall'] == 1
    assert summary['redundant_returned'] == 1


def test_gold_fact_annotations_never_enter_selector_candidates():
    seen = []

    def selector(candidates, query, **kwargs):
        seen.extend(candidates)
        return candidates[:8]

    data = {'as_of': '2026-10-22T12:00:00Z', 'fact_groups': {'raw': 'fact'},
            'memories': [{**row('raw'), 'aliases': []}],
            'queries': [{'id': 'q', 'split': 'test', 'query': '用例要求',
                         'context': {'workspace_id': 'ws-a'}, 'relevant_ids': ['fact'], 'required_ids': ['fact']}]}
    actual = bench.run_queries(data, 'topical_pool', {'weights': [1, 1, 1], 'coverage': [1, 2]},
                               {'rrf_k': 60, 'candidate_limit': 50, 'result_limit': 8}, 'test', selector=selector)
    assert actual[0]['actual_ids'] == ['raw']
    assert actual[0]['evaluation_ids'] == ['fact']
    assert all('fact_groups' not in r and 'fact_id' not in r and 'relevant_ids' not in r for r in seen)


def test_robustness_dataset_groups_and_gold_are_well_formed():
    data = json.loads((Path(__file__).parent / 'robustness-fixtures.json').read_text())
    assert len(data['queries']) == 144
    groups = {s: {q['group'] for q in data['queries'] if q['split'] == s}
              for s in ('calibration', 'validation', 'test')}
    assert [len(groups[s]) for s in groups] == [18, 9, 9]
    assert not groups['calibration'] & groups['validation']
    assert not groups['validation'] & groups['test']
    assert not groups['calibration'] & groups['test']
    known = set(data['fact_groups'].values())
    assert all(set(q['relevant_ids']) <= known for q in data['queries'])


def test_expansion_cannot_replace_the_original_subject_anchor():
    rows = [row('right', '订单', 'TC-41是订单冒烟；按此执行。'),
            row('contrast', '订单', 'TC-410属于扩展；它与TC-41不同。')]
    actual = robust.select_results(rows, 'TC-41', expansions=['订单'], route='topical_pool')
    assert [r['id'] for r in actual] == ['right']


def test_duplicate_member_annotations_do_not_credit_a_missing_independent_fact():
    def selector(candidates, query, **kwargs):
        return [{**candidates[0], 'duplicate_ids': ['second']}]

    data = {'as_of': '2026-10-22T12:00:00Z', 'fact_groups': {'first': 'fact-a', 'second': 'fact-b'},
            'memories': [{**row('first'), 'aliases': []}, {**row('second'), 'aliases': []}],
            'queries': [{'id': 'q', 'split': 'test', 'query': '用例要求',
                         'context': {'workspace_id': 'ws-a'}, 'relevant_ids': ['fact-a', 'fact-b'], 'required_ids': []}]}
    actual = bench.run_queries(data, 'topical_exact', {'weights': [1, 1, 1], 'coverage': [1, 2]},
                               {'rrf_k': 60, 'candidate_limit': 50, 'result_limit': 8}, 'test', selector=selector)
    assert bench.summarize(actual)['macro_recall'] == 0.5
