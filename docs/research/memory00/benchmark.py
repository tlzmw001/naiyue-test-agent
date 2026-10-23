"""Two-stage reproducible lexical comparison; no production/provider access."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
from statistics import mean, median
import sys
from time import perf_counter_ns

from .retrieval import METHODS, ResearchIndex, timestamp


HERE = Path(__file__).resolve().parent


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def check_fixture_hash(raw, protocol):
    if digest(raw) != protocol['fixtures_sha256']:
        raise ValueError('fixture hash mismatch; do not silently change frozen examples')


def measure(query, actual, *, limit):
    ids = [row['id'] for row in actual]
    relevant, required = set(query['relevant_ids']), set(query['required_ids'])
    hits = len(set(ids) & relevant)
    return {'id': query['id'], 'actual_ids': ids,
        'recall_at_8': hits / len(relevant) if relevant else None,
        'precision_at_8': hits / limit,
        'precision_returned_at_8': hits / len(ids) if ids else None,
        'required_hits': len(set(ids) & required), 'required_total': len(required),
        'forbidden_hits': sorted(set(ids) & set(query.get('forbidden_ids', []))),
        'no_answer_correct': not ids if query.get('no_answer') else None}


def exclusions(data, context):
    excluded = {}
    now = timestamp(data['as_of'])
    for row in data['memories']:
        reasons = []
        if row['scope'] == 'workspace' and row['workspace_id'] != context['workspace_id']:
            reasons.append('other_workspace')
        if row['status'] != 'active':
            reasons.append(row['status'])
        if row.get('forgotten'):
            reasons.append('forgotten')
        if row.get('expires_at') and timestamp(row['expires_at']) <= now:
            reasons.append('expired')
        for field in ('machine', 'target', 'module'):
            if row.get(field) is not None and context.get(field) is not None and row[field] != context[field]:
                reasons.append(field + '_mismatch')
        if reasons:
            excluded[row['id']] = reasons
    return excluded


def run_queries(data, protocol, method, weights, split):
    rows = []
    with ResearchIndex(data['memories'], method=method, weights=weights,
                       rrf_k=protocol['rrf_k'], candidate_limit=protocol['candidate_limit']) as index:
        for query in data['queries']:
            if query['split'] != split:
                continue
            context = {**data['context'], **query.get('context', {})}
            blocked = exclusions(data, context)
            started = perf_counter_ns()
            actual = index.search(query['query'], context, data['as_of'],
                limit=protocol['result_limit'], expansions=query.get('expansions', []))
            elapsed = (perf_counter_ns() - started) / 1_000_000
            scored = measure({**query, 'forbidden_ids': list(set(blocked) |
                set(query.get('forbidden_ids', [])))}, actual, limit=protocol['result_limit'])
            rows.append({**scored, 'query': query['query'], 'context': context,
                'relevant_ids': query['relevant_ids'], 'required_ids': query['required_ids'],
                'excluded': blocked, 'elapsed_ms': elapsed,
                'returned_utf8_bytes': len(json.dumps(actual, ensure_ascii=False).encode()),
                'actual': actual})
    return rows


def summarize(rows):
    recalls = [row['recall_at_8'] for row in rows if row['recall_at_8'] is not None]
    precisions = [row['precision_returned_at_8'] for row in rows
                  if row['precision_returned_at_8'] is not None]
    return {'queries': len(rows), 'recall_at_8': mean(recalls) if recalls else None,
        'precision_returned_at_8': mean(precisions) if precisions else None,
        'required_hits': sum(row['required_hits'] for row in rows),
        'required_total': sum(row['required_total'] for row in rows),
        'forbidden_count': sum(len(row['forbidden_hits']) for row in rows),
        'no_answer_correct': sum(row['no_answer_correct'] is True for row in rows),
        'no_answer_total': sum(row['no_answer_correct'] is not None for row in rows),
        'median_elapsed_ms': median(row['elapsed_ms'] for row in rows) if rows else None,
        'returned_utf8_bytes': sum(row['returned_utf8_bytes'] for row in rows)}


def _quality(summary):
    return (-summary['forbidden_count'], summary['required_hits'],
        summary['no_answer_correct'], summary['recall_at_8'] or 0,
        summary['precision_returned_at_8'] or 0)


def calibrate(data, protocol):
    profiles = {}
    for method in METHODS:
        trials = []
        for weights in protocol['weight_candidates']:
            rows = run_queries(data, protocol, method, weights, 'calibration')
            trials.append({'weights': weights, 'summary': summarize(rows), 'queries': rows})
        # Fixed preference on ties: retain stronger title/alias than body, then smaller weights.
        selected = max(trials, key=lambda trial: (_quality(trial['summary']),
            trial['weights'][0] > trial['weights'][1] > trial['weights'][2],
            -sum(trial['weights'])))
        profiles[method] = {**selected, 'trials': [
            {'weights': trial['weights'], 'summary': trial['summary']} for trial in trials]}
    return profiles


def manifest(raw, protocol_raw):
    return {'fixtures': digest(raw), 'protocol': digest(protocol_raw),
        'retrieval.py': digest((HERE / 'retrieval.py').read_bytes()),
        'benchmark.py': digest(Path(__file__).read_bytes())}


def runtime():
    with sqlite3.connect(':memory:') as connection:
        options = [row[0] for row in connection.execute('PRAGMA compile_options')]
    return {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
        'platform': platform.platform(), 'compile_options': options,
        'provider_calls': 0, 'databases': 'independent in-memory workspace and personal'}


def gate(summary):
    return (summary['forbidden_count'] == 0
        and summary['required_hits'] == summary['required_total']
        and summary['no_answer_correct'] == summary['no_answer_total'])


def evaluate(data, protocol, frozen):
    routes = {}
    for method in METHODS:
        weights = frozen['profiles'][method]['weights']
        rows = run_queries(data, protocol, method, weights, 'holdout')
        calibration = frozen['profiles'][method]['summary']
        holdout = summarize(rows)
        routes[method] = {'weights': weights, 'calibration': calibration,
            'holdout': holdout, 'holdout_queries': rows,
            'passes_gates': gate(calibration) and gate(holdout)}
    qualified = [method for method in METHODS if routes[method]['passes_gates']]
    # Validation chooses the route; it is not an independent final test of that choice.
    recommended = max(qualified, key=lambda method: (
        _quality(routes[method]['holdout']), -METHODS.index(method))) if qualified else None
    return {'status': 'passed' if recommended else 'needs_revision',
        'recommended_method': recommended, 'routes': routes,
        'scope_note': 'held-out validation participates in route selection, not independent final testing',
        'extraction_cases_run': 0, 'workflow_cases_run': 0}


def save_new(path, data):
    # Exclusive creation, including against an existing symlink. Never replace evidence.
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('calibrate', 'evaluate'), required=True)
    parser.add_argument('--fixtures', type=Path, default=HERE / 'fixtures.json')
    parser.add_argument('--protocol', type=Path, default=HERE / 'protocol.json')
    parser.add_argument('--frozen', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError('output already exists')
    raw, protocol_raw = args.fixtures.read_bytes(), args.protocol.read_bytes()
    data, protocol = json.loads(raw), json.loads(protocol_raw)
    check_fixture_hash(raw, protocol)
    hashes = manifest(raw, protocol_raw)
    common = {'created_at': datetime.now(timezone.utc).isoformat(),
        'input_hashes': hashes, 'runtime': runtime()}
    if args.stage == 'calibrate':
        result = {**common, 'profiles': calibrate(data, protocol),
            'selection_uses': 'calibration labels only; validation not evaluated yet'}
    else:
        if args.frozen is None:
            raise ValueError('evaluation requires frozen calibration')
        frozen_raw = args.frozen.read_bytes()
        frozen = json.loads(frozen_raw)
        if frozen['input_hashes'] != hashes:
            raise ValueError('code/input changed since calibration; freeze again and disclose any validation reuse')
        result = {**common, 'frozen_sha256': digest(frozen_raw), **evaluate(data, protocol, frozen)}
    save_new(args.output, result)
    print(json.dumps({'stage': args.stage, 'output': str(args.output),
        'status': result.get('status', 'frozen'), 'recommended_method': result.get('recommended_method')}))
    return 0 if result.get('status', 'passed') == 'passed' else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f'memory00_experiment_failed error_type={type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
