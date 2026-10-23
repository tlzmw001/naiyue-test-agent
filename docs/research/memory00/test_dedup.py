"""Single-variable exact display deduplication and paired-driver contracts."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from docs.research.memory00 import benchmark_tuning as bench
from docs.research.memory00 import retrieval_robustness as robust


HERE = Path(__file__).parent


def record(identity, body='用例要求包含断言。', **changes):
    return {'id': identity, 'scope': 'workspace', 'workspace_id': 'ws-a',
            'kind': 'rule', 'status': 'active', 'title': '用例要求', 'aliases': '',
            'body': body, 'score': 0.01, 'version': 1,
            'machine': None, 'target': None, 'module': None, **changes}


def test_collapse_precedes_result_limit_and_preserves_order_and_input():
    rows = [record(f'copy{i}') for i in range(8)] + [
        record('cleanup', '用例要求必须清理。'), record('approval', '用例要求必须审批。')]
    original = deepcopy(rows)
    baseline = robust.select_results(rows, '用例要求', route='cascade_base')
    actual = robust.select_results(rows, '用例要求', route='cascade_exact')
    assert [r['id'] for r in baseline] == [f'copy{i}' for i in range(8)]
    assert [r['id'] for r in actual] == ['copy0', 'cleanup', 'approval']
    assert actual[0]['duplicate_ids'] == [f'copy{i}' for i in range(1, 8)]
    assert rows == original


def test_exact_display_does_not_fill_from_weak_tier():
    rows = [record('first'), record('second'),
            record('weak', '用例命名要求定义类名。', title='用例命名要求')]
    actual = robust.select_results(rows, '用例要求', route='cascade_exact')
    assert [r['id'] for r in actual] == ['first']
    assert actual[0]['duplicate_ids'] == ['second']


@pytest.mark.parametrize('change', [
    {'body': 'if enabled:\n    run()\n    cleanup()'},
    {'scope': 'personal'}, {'workspace_id': 'ws-b'}, {'kind': 'decision'},
    {'machine': 'windows'}, {'target': 'production'}, {'module': 'billing'},
    {'body': '禁止执行'}, {'body': '使用0.5'}, {'body': '使用TC-410'},
    {'title': '用例要求 '},
])
def test_distinct_text_or_boundaries_survive_exact_display(change):
    rows = [record('first', 'if enabled:\n    run()\ncleanup()'),
            record('second', 'if enabled:\n    run()\ncleanup()', **{k: v for k, v in change.items() if k != 'body'})]
    rows[1].update(change)
    actual = robust.select_results(rows, '用例要求', route='cascade_exact')
    assert [r['id'] for r in actual] == ['first', 'second']
    assert all(not r['duplicate_ids'] for r in actual)


def paired_rows(candidates):
    return [{'id': 'q', 'query': '用例要求', 'relevant_ids': ['right'],
             'required_ids': ['right'], 'actual_ids': ['right'], 'evaluation_ids': ['right'],
             'forbidden_hits': [], 'candidate_ids': candidates, 'actual': []}]


def test_compare_rejects_different_candidate_order(tmp_path, monkeypatch):
    protocol = json.loads((HERE / 'dedup-protocol.json').read_text())
    for name in protocol['observed_datasets']:
        (tmp_path / name).write_text(json.dumps({'queries': [{'split': 'observed'}]}))
    monkeypatch.setattr(bench, 'HERE', tmp_path)

    def runner(data, route, config, protocol, split):
        return paired_rows(['a', 'b'] if route == 'cascade_base' else ['b', 'a'])

    with pytest.raises(ValueError, match='candidate'):
        bench.compare_observed(protocol, runner=runner)


def test_comparison_gate_reports_new_noise_even_when_recall_is_preserved(tmp_path, monkeypatch):
    protocol = json.loads((HERE / 'dedup-protocol.json').read_text())
    for name in protocol['observed_datasets']:
        (tmp_path / name).write_text(json.dumps({'queries': [{'split': 'observed'}]}))
    monkeypatch.setattr(bench, 'HERE', tmp_path)

    def runner(data, route, config, protocol, split):
        rows = paired_rows(['right', 'wrong'])
        if route == 'cascade_exact':
            rows[0].update(actual_ids=['right', 'wrong'], evaluation_ids=['right', 'wrong'])
        return rows

    result = bench.compare_observed(protocol, runner=runner)
    assert result['status'] == 'measured'
    assert not result['candidate_gate_passes']
    for value in result['datasets'].values():
        assert value['comparisons'][0]['new_irrelevant_facts'] == ['wrong']
        assert value['comparisons'][0]['lost_relevant_facts'] == []


def test_real_cli_compare_is_wired_and_does_not_overwrite(tmp_path):
    output = tmp_path / 'comparison.json'
    args = ['--protocol', str(HERE / 'dedup-protocol.json'), '--stage', 'compare', '--output', str(output)]
    assert bench.main(args) == 0
    result = json.loads(output.read_text())
    assert result['selection_uses'] == 'observed data only; no tuning or independent test claim'
    assert set(result['datasets']) == {'fixtures.json', 'tuning-fixtures.json', 'robustness-fixtures.json'}
    assert sum(v['paired_queries'] for v in result['datasets'].values()) == 312
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        bench.main(args)
    assert output.read_bytes() == before


@pytest.mark.parametrize('stage', ['freeze', 'test'])
def test_dedup_protocol_rejects_selection_stages(tmp_path, stage):
    with pytest.raises(ValueError, match='compare'):
        bench.main(['--protocol', str(HERE / 'dedup-protocol.json'), '--stage', stage,
                    '--output', str(tmp_path / 'out.json')])


def test_compare_rejects_changed_observed_fixture_hash(tmp_path):
    protocol = json.loads((HERE / 'dedup-protocol.json').read_text())
    protocol['observed_datasets']['tuning-fixtures.json'] = '0' * 64
    path = tmp_path / 'protocol.json'
    path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match='observed'):
        bench.main(['--protocol', str(path), '--stage', 'compare', '--output', str(tmp_path / 'out.json')])


def test_unchanged_existing_no_answer_error_is_reported_without_becoming_a_new_error(tmp_path, monkeypatch):
    protocol = json.loads((HERE / 'dedup-protocol.json').read_text())
    for name in protocol['observed_datasets']:
        (tmp_path / name).write_text(json.dumps({'queries': [{'split': 'observed'}]}))
    monkeypatch.setattr(bench, 'HERE', tmp_path)

    def runner(data, route, config, protocol, split):
        rows = paired_rows(['noise'])
        rows[0].update(actual_ids=['noise'], evaluation_ids=['noise'], relevant_ids=[],
                       required_ids=[], no_answer=True)
        return rows

    result = bench.compare_observed(protocol, runner=runner)
    assert result['candidate_gate_passes']
    for value in result['datasets'].values():
        assert value['routes']['cascade_exact']['summary']['no_answer_errors'] == 1
        assert not value['comparisons'][0]['new_no_answer_error']


def test_non_dedup_protocol_cannot_use_compare(tmp_path):
    with pytest.raises(ValueError, match='requires dedup'):
        bench.main(['--protocol', str(HERE / 'robustness-protocol.json'), '--stage', 'compare',
                    '--output', str(tmp_path / 'out.json')])
