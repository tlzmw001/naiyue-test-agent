"""Frozen Wiki evaluation contracts. S0 plan/offline never construct a model or user DB."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.memory_evaluation_cases import write_report

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'tests/data/memory_wiki'
SPLITS = ('calibration', 'validation', 'holdout')
TOPICS = {'collaboration', 'working_agreements', 'stable_knowledge'}


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_suite(*, data=None, protocol=None):
    frozen = data is None
    protocol = deepcopy(protocol) if protocol is not None else json.loads((DATA / 'protocol.json').read_text())
    if frozen:
        if _hash(DATA / 'fixtures.json') != protocol['fixtures_sha256']:
            raise ValueError('fixture_changed')
        data = json.loads((DATA / 'fixtures.json').read_text())
    if data.get('synthetic') is not True or protocol.get('protocol') != 'wiki_v1':
        raise ValueError('fixture_contract')
    cases = data['cases']
    ids, families, coverage = set(), {}, set()
    for case in cases:
        identity, split = case['case_id'], case['split']
        if identity in ids or split not in SPLITS or families.setdefault(case['family_id'], split) != split:
            raise ValueError('fixture_identity_or_split_leakage')
        ids.add(identity)
        coverage.update(case['coverage'])
        source = case['input']
        expected = case['expected']
        if not set(expected['allowed_scopes']) <= {'personal', 'workspace'}:
            raise ValueError('fixture_scope')
        if 'workspace' in expected['allowed_scopes'] and source['workspace_id'] is None:
            raise ValueError('fixture_scope_binding')
        if expected['topic'] not in TOPICS | {None}:
            raise ValueError('fixture_topic')
        if not all(expected.get(k) for k in ('allowed_changes', 'preserve', 'forbidden')):
            raise ValueError('fixture_labels')
        if not source['sources'] or any(not s['text'] or type(s['occurred_at']) is not int for s in source['sources']):
            raise ValueError('fixture_sources')
        if source['wiki']['generation'] != 'wiki_v1' or any(
                m.get('generation') != 'wiki_v1' for m in source['wiki']['memories']):
            raise ValueError('fixture_legacy_seed')
    if any(sum(c['split'] == s for c in cases) != 8 for s in SPLITS):
        raise ValueError('fixture_balance')
    if coverage != set(protocol['required_coverage']) or coverage != {f'W{i:02}' for i in range(1, 25)}:
        raise ValueError('fixture_coverage')
    return cases, protocol


def provenance():
    # Git's file inventory includes uncommitted implementation; never hash credentials/config files.
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others',
        '--exclude-standard', '--', 'naiyue_test_agent', 'scripts', 'tests'], cwd=ROOT).decode().split('\0')
    digest = hashlib.sha256()
    count = 0
    for name in sorted(set(paths) - {''}):
        path = ROOT / name
        if path.is_file():
            digest.update(name.encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
            count += 1
    return dict(fixtures_sha256=_hash(DATA / 'fixtures.json'), protocol_sha256=_hash(DATA / 'protocol.json'),
                driver_sha256=_hash(Path(__file__)), source_sha256=digest.hexdigest(), source_files=count)


def plan(split):
    if split not in SPLITS:
        raise ValueError('split_invalid')
    cases, protocol = load_suite()
    selected = [case for case in cases if case['split'] == split]
    return dict(protocol=protocol['protocol'], split=split, status='planned',
        provenance=provenance(), model_id=None, model_requests=0, product_quality_passed=None,
        runtime_status=protocol['runtime_status'],
        inputs=[dict(case_id=c['case_id'], **deepcopy(c['input'])) for c in selected])


def validate_report(report):
    expected = plan(report['split'])
    if report['protocol'] != expected['protocol'] or report['provenance'] != expected['provenance']:
        raise ValueError('report_provenance_changed')
    rows = report['cases']
    identities = [row['case_id'] for row in rows]
    if len(set(identities)) != len(identities) or set(identities) != {r['case_id'] for r in expected['inputs']}:
        raise ValueError('report_incomplete_or_duplicate')
    completed = 0
    for row in rows:
        if row['status'] not in ('completed', 'failed', 'skipped'):
            raise ValueError('report_status')
        completed += row['status'] == 'completed'
        observations = row['observations']
        if observations.get('preview') is True and observations.get('complete') is True:
            raise ValueError('report_preview_not_complete')
        review = row['semantic_review']
        if review is not None:
            if review.get('reviewer') not in ('assistant', 'human') or type(review.get('passed')) is not bool or not review.get('reason'):
                raise ValueError('report_review_attribution')
            if review['passed'] and row['status'] != 'completed':
                raise ValueError('report_failed_case_approved')
    return dict(status='report_contract_validated', expected_cases=len(rows), completed=completed,
                failed=sum(r['status'] == 'failed' for r in rows),
                skipped=sum(r['status'] == 'skipped' for r in rows), product_quality_passed=None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('plan', 'offline', 'background', 'foreground'), required=True)
    parser.add_argument('--split', choices=SPLITS, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, help='Optional existing report to validate offline')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--model-id')
    args = parser.parse_args(argv)
    if args.mode in ('background','foreground'):
        import asyncio
        from scripts.memory_wiki_runtime_evaluation import run
        report = asyncio.run(run(args))
        return int(bool(report.get('cleanup_error') or any(
            case['status'] == 'failed' for case in report['cases'])))
    if args.report and args.mode != 'offline':
        parser.error('--report requires offline')
    result = plan(args.split)
    # Do not persist model-facing inputs with labels. Plan records IDs; assets remain authoritative.
    result['case_ids'] = [row['case_id'] for row in result.pop('inputs')]
    if args.mode == 'offline':
        result['status'] = 'contracts_validated'
        if args.report:
            report = json.loads(args.report.read_text())
            if report['split'] != args.split:
                raise ValueError('report_split_mismatch')
            result['report_validation'] = validate_report(report)
    result['mode'] = args.mode
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_report(args.output_dir / 'manifest.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    raise SystemExit(main())
