from copy import deepcopy
import json
import subprocess
import sys

import pytest

from scripts.memory_wiki_evaluation import load_suite, plan, validate_report


def test_suite_is_balanced_and_separates_model_input_from_expectations():
    cases, protocol = load_suite()
    assert len(cases) == 24
    assert {s: sum(c['split'] == s for c in cases) for s in protocol['splits']} == {
        'calibration': 8, 'validation': 8, 'holdout': 8}
    for split in protocol['splits']:
        result = plan(split)
        assert len(result['inputs']) == 8 and result['model_requests'] == 0
        assert all('expected' not in row and 'coverage' not in row for row in result['inputs'])
        assert result['product_quality_passed'] is None


@pytest.mark.parametrize('mutation,error', [
    ('scope', 'scope'), ('family', 'split'), ('legacy', 'legacy'),
    ('coverage', 'coverage'), ('count', 'balance'),
])
def test_invalid_suite_is_rejected(mutation, error):
    cases, protocol = load_suite()
    cases = deepcopy(cases)
    if mutation == 'scope': cases[0]['expected']['allowed_scopes'] = ['global']
    if mutation == 'family': cases[8]['family_id'] = cases[0]['family_id']
    if mutation == 'legacy': cases[0]['input']['wiki']['memories'].append({'generation': 'legacy143bm'})
    if mutation == 'coverage': cases[0]['coverage'] = []
    if mutation == 'count': cases.pop()
    with pytest.raises(ValueError, match=error):
        load_suite(data={'synthetic': True, 'cases': cases}, protocol=protocol)


def report_for(split='calibration'):
    p = plan(split)
    return dict(protocol=p['protocol'], split=split, provenance=p['provenance'],
        cases=[dict(case_id=c['case_id'], status='failed', observations={},
                    semantic_review=None) for c in p['inputs']])


def test_failed_cases_are_retained_and_not_counted_as_success():
    result = validate_report(report_for())
    assert result['failed'] == 8 and result['completed'] == 0
    assert result['product_quality_passed'] is None


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'hash', 'preview', 'fake_human'])
def test_report_rejects_missing_cases_changed_sources_and_false_completeness(mutation):
    report = report_for()
    if mutation == 'missing': report['cases'].pop()
    if mutation == 'duplicate': report['cases'][-1] = report['cases'][0]
    if mutation == 'hash': report['provenance']['fixtures_sha256'] = 'bad'
    if mutation == 'preview':
        report['cases'][0]['observations'] = {'preview': True, 'complete': True}
    if mutation == 'fake_human':
        report['cases'][0]['semantic_review'] = {'reviewer': 'model', 'passed': True}
    with pytest.raises(ValueError): validate_report(report)


def test_cli_offline_no_network_and_exclusive_output(tmp_path):
    command = [sys.executable, 'scripts/memory_wiki_evaluation.py', '--mode', 'offline',
               '--split', 'calibration', '--output-dir', str(tmp_path)]
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    assert manifest['model_requests'] == 0 and manifest['product_quality_passed'] is None
    assert manifest['status'] == 'contracts_validated'
    assert subprocess.run(command, capture_output=True).returncode != 0


def test_missing_runtime_config_fails_without_creating_success_report(tmp_path):
    completed = subprocess.run([sys.executable, 'scripts/memory_wiki_evaluation.py',
        '--mode', 'foreground', '--split', 'calibration', '--config', str(tmp_path/'missing.json'), '--output-dir', str(tmp_path)],
        capture_output=True, text=True)
    assert completed.returncode != 0
    assert 'cannot read model configuration' in completed.stderr
    assert not (tmp_path / 'manifest.json').exists()
    assert not (tmp_path / 'report.json').exists()


def test_boundary_scenarios_have_reachable_material_and_eligible_gc_target():
    cases, _ = load_suite()
    indexed = {c['case_id']: c['input'] for c in cases}
    directory = indexed['W13']['wiki']
    assert len(directory['pages']) == 80
    assert {m['page_id'] for m in directory['memories']} == {p['page_id'] for p in directory['pages']}
    race = indexed['W16']
    assert race['wiki']['memories'][0]['expires_at'] <= race['now'] - 7 * 86400
    links = indexed['W18']['wiki']
    assert {m['page_id'] for m in links['memories']} >= {'P1', 'PRIVATE'}


def test_foreground_calibration_covers_reads_writes_and_preserves_live_seed_lifetimes():
    cases, _ = load_suite()
    by_id = {c['case_id']: c['input'] for c in cases}
    foreground = {c['case_id'] for c in cases if c['split'] == 'calibration'
                  and 'foreground' in c['input']['scenario']['execution_modes']}
    assert foreground == {'W01', 'W04', 'W07', 'W10', 'W13', 'W22'}
    for case_id in ('W07', 'W08', 'W17', 'W22'):
        source = by_id[case_id]
        item, = source['wiki']['memories']
        assert item['evidence_at'] < source['now'] < item['expires_at']
        assert item['expires_at'] - item['evidence_at'] == 30 * 86400
    for case_id in ('W15', 'W16'):
        source = by_id[case_id]
        assert source['wiki']['memories'][0]['expires_at'] <= source['now']
