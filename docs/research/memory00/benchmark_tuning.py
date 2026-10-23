"""Calibrate, freeze validation choice, then test without changing selection."""

import argparse
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
from pathlib import Path
import sqlite3
from statistics import mean
import sys
from time import perf_counter_ns

from .benchmark import exclusions, runtime, save_new
from .retrieval import ResearchIndex
from .retrieval_tuning import select_results


HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(protocol_path=None):
    path = protocol_path or HERE / 'tuning-protocol.json'
    protocol = json.loads(path.read_text())
    fixture_name = protocol.get('fixture_name', 'tuning-fixtures.json')
    if Path(fixture_name).name != fixture_name or not fixture_name.endswith('.json'):
        raise ValueError('fixture must be a research JSON basename')
    names = [fixture_name, 'retrieval.py', 'retrieval_tuning.py',
             'benchmark_tuning.py', 'benchmark.py', 'fixtures.json']
    if protocol.get('experiment') in ('robustness', 'dedup'):
        names.append('retrieval_robustness.py')
    if protocol.get('experiment') == 'dedup':
        names.extend(('tuning-fixtures.json', 'robustness-fixtures.json'))
    return {**{name: digest(HERE / name) for name in names}, path.name: digest(path)}


def summarize(rows):
    positive = [row for row in rows if row['relevant_ids']]
    recalled, f_scores, f1_scores, first, reciprocal = [], [], [], [], []
    stratum_recalls = {}
    correct = record_correct = redundant = total = forbidden = negative_errors = negatives = 0
    for row in rows:
        actual, relevant = row.get('evaluation_ids', row['actual_ids']), set(row['relevant_ids'])
        hits = len(set(actual) & relevant)
        correct += hits
        record_correct += sum(identity in relevant for identity in actual)
        redundant += len(actual) - len(set(actual))
        total += len(actual)
        forbidden += len(row['forbidden_hits'])
        if relevant:
            precision, recall = hits / len(actual) if actual else 0, hits / len(relevant)
            recalled.append(recall)
            stratum_recalls.setdefault(row.get('stratum', 'all'), []).append(recall)
            f_scores.append(1.25 * precision * recall / (0.25 * precision + recall)
                            if precision and recall else 0)
            f1_scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0)
            ranks = [i for i, identity in enumerate(actual, 1) if identity in relevant]
            first.append(bool(actual) and actual[0] in relevant)
            reciprocal.append(1 / ranks[0] if ranks else 0)
        if row.get('no_answer'):
            negatives += 1
            negative_errors += bool(actual)
    return {'queries': len(rows), 'positive_queries': len(positive),
        'micro_precision': correct / total if total else 0,
        'record_precision': record_correct / total if total else 0,
        'redundant_returned': redundant,
        'relevant_returned': correct, 'returned_count': total,
        'macro_recall': mean(recalled) if recalled else 0,
        'positive_macro_f0_5': mean(f_scores) if f_scores else 0,
        'positive_macro_f1': mean(f1_scores) if f1_scores else 0,
        'stratum_macro_recall': {key: mean(values) for key, values in stratum_recalls.items()},
        'worst_stratum_recall': min((mean(values) for values in stratum_recalls.values()), default=0),
        'hit_at_1': mean(first) if first else 0, 'mrr_at_8': mean(reciprocal) if reciprocal else 0,
        'no_answer_errors': negative_errors, 'no_answer_queries': negatives,
        'no_answer_error_rate': negative_errors / negatives if negatives else 0,
        'forbidden_count': forbidden, 'average_returned': total / len(rows) if rows else 0,
        'returned_utf8_bytes': sum(row.get('returned_utf8_bytes', 0) for row in rows),
        'query_time_ms': sum(row.get('elapsed_ms', 0) for row in rows)}


def run_queries(data, route, config, protocol, split, *, selector=None):
    selector = selector or select_results
    rows = []
    method = 'substring' if route == 'substring' else 'adapted'
    with ResearchIndex(data['memories'], method=method, weights=config['weights'],
                       rrf_k=protocol['rrf_k'], candidate_limit=protocol['candidate_limit']) as index:
        for query in data['queries']:
            if query['split'] != split:
                continue
            context = {**data.get('context', {}), **query.get('context', {})}
            blocked = exclusions(data, context)
            started = perf_counter_ns()
            candidates = index.search(query['query'], context, data['as_of'],
                limit=protocol['candidate_limit'], expansions=query.get('expansions', []))
            actual = selector(candidates, query['query'], route=route,
                weights=config['weights'], coverage=config['coverage'], limit=protocol['result_limit'],
                expansions=query.get('expansions', []), **config.get('options', {}))
            ids = [row['id'] for row in actual]
            evaluation_ids = [data.get('fact_groups', {}).get(identity, identity) for identity in ids]
            forbidden = set(blocked) | set(query.get('forbidden_ids', []))
            rows.append({**query, 'actual_ids': ids, 'forbidden_hits': sorted(set(ids) & forbidden),
                'evaluation_ids': evaluation_ids,
                'missing_ids': sorted(set(query['relevant_ids']) - set(evaluation_ids)),
                'extra_ids': sorted(set(evaluation_ids) - set(query['relevant_ids'])),
                'candidate_count': len(candidates), 'elapsed_ms': (perf_counter_ns() - started) / 1_000_000,
                'candidate_ids': [row['id'] for row in candidates],
                'returned_utf8_bytes': len(json.dumps(actual, ensure_ascii=False).encode()), 'actual': actual})
    return rows


def quality(summary, *, balanced=False):
    if balanced:
        return (-summary['forbidden_count'], -summary['no_answer_errors'],
            summary['worst_stratum_recall'], summary['positive_macro_f1'], summary['micro_precision'],
            summary['hit_at_1'], -summary['redundant_returned'], -summary['returned_count'])
    return (-summary['forbidden_count'], -summary['no_answer_errors'],
        summary['positive_macro_f0_5'], summary['macro_recall'], summary['hit_at_1'],
        -summary['returned_count'])


def calibrate(data, protocol, *, runner=None):
    runner = runner or run_queries
    profiles = {}
    for route in protocol['routes']:
        trials = []
        coverages = [[1, 2]] if route in ('substring', 'adapted_or') else protocol['coverages']
        configs = protocol.get('configs', {}).get(route) or [
            {'weights': weights, 'coverage': coverage} for weights in protocol['weights'] for coverage in coverages]
        for config in configs:
            rows = runner(data, route, config, protocol, 'calibration')
            trials.append({**config, 'summary': summarize(rows), 'queries': rows})
        profiles[route] = max(trials, key=lambda trial: (quality(trial['summary'], balanced=protocol.get('selection') == 'balanced_robustness'),
            -sum(trial['weights']), trial['coverage'][0] / trial['coverage'][1],
            trial.get('options', {}).get('diversity_lambda', 1)))
        profiles[route] = {**profiles[route], 'trials': [
            {key: value for key, value in trial.items() if key != 'queries'} for trial in trials]}
    return profiles


def validate(data, protocol, profiles, *, runner=None):
    runner = runner or run_queries
    routes = {}
    for route, config in profiles.items():
        rows = runner(data, route, config, protocol, 'validation')
        routes[route] = {'summary': summarize(rows), 'queries': rows}
    old = json.loads((HERE / 'fixtures.json').read_text())
    regressions = {}
    for route, config in profiles.items():
        rows = [row for split in ('calibration', 'holdout') for row in
                runner(old, route, config, protocol, split)]
        passes = all(set(row['required_ids']) <= set(row['actual_ids'])
                     and not row['forbidden_hits']
                     and (not row.get('no_answer') or not row['actual_ids']) for row in rows)
        regressions[route] = {'passes': passes, 'summary': summarize(rows), 'queries': rows}
    eligible = [route for route in protocol['routes'] if regressions[route]['passes']]
    chosen = max(eligible, key=lambda route: (quality(routes[route]['summary'], balanced=protocol.get('selection') == 'balanced_robustness'),
        -protocol['routes'].index(route))) if eligible else None
    fixed_config = protocol.get('ablation_config', {'weights': [3, 2, 1], 'coverage': [2, 3]})
    ablation = {route: summarize(runner(data, route, fixed_config, protocol, 'validation'))
                for route in protocol['routes']}
    return {'selected_route': chosen, 'routes': routes, 'regressions': regressions,
            'fixed_config_ablation': {'config': fixed_config, 'summaries': ablation}}


def evaluate(data, protocol, frozen, *, runner=None):
    runner = runner or run_queries
    if frozen['validation']['selected_route'] is None:
        raise ValueError('no eligible frozen route; do not spend the final test on an invalid selection')
    routes = {}
    for route, config in frozen['profiles'].items():
        rows = runner(data, route, config, protocol, 'test')
        routes[route] = {'config': {key: config[key] for key in ('weights', 'coverage', 'options') if key in config},
                         'summary': summarize(rows), 'queries': rows}
    selected = frozen['validation']['selected_route']
    selected_summary = routes[selected]['summary']
    old = json.loads((HERE / 'fixtures.json').read_text())
    old_rows = [row for split in ('calibration', 'holdout') for row in
                runner(old, selected, frozen['profiles'][selected], protocol, split)]
    regression = summarize(old_rows)
    return {'selected_route': selected, 'selection_uses': 'calibration+validation only; never test labels',
        'test_routes': routes, 'old_regression': {'summary': regression, 'queries': old_rows},
        'status': 'measured' if selected_summary['forbidden_count'] == 0 else 'failed_safety_gate'}


def compare_observed(protocol, *, runner):
    names = ('fixtures.json', 'tuning-fixtures.json', 'robustness-fixtures.json')
    if set(protocol['observed_datasets']) != set(names):
        raise ValueError('comparison requires the three fixed observed datasets')
    if (protocol['routes'] != ['cascade_base', 'cascade_exact']
            or protocol['config'] != {'weights': [1, 1, 1], 'coverage': [1, 2]}
            or (protocol['rrf_k'], protocol['candidate_limit'], protocol['result_limit']) != (60, 50, 8)):
        raise ValueError('comparison requires fixed 00b parameters and routes')
    datasets = {}
    for name in names:
        data = json.loads((HERE / name).read_text())
        splits = sorted({query['split'] for query in data['queries']})
        routes = {route: [row for split in splits for row in
                  runner(data, route, protocol['config'], protocol, split)] for route in protocol['routes']}
        comparisons = []
        base, exact = routes['cascade_base'], routes['cascade_exact']
        if len(base) != len(exact):
            raise ValueError('paired candidate/query counts differ')
        for left, right in zip(base, exact, strict=True):
            if left['id'] != right['id'] or left['candidate_ids'] != right['candidate_ids']:
                raise ValueError('paired candidate order differs')
            gold = set(left['relevant_ids'])
            before = set(left['evaluation_ids'])
            after = set(right['evaluation_ids'])
            comparisons.append({'id': left['id'], 'query': left['query'],
                'gained_relevant_facts': sorted((after - before) & gold),
                'lost_relevant_facts': sorted((before - after) & gold),
                'new_irrelevant_facts': sorted((after - before) - gold),
                'removed_irrelevant_facts': sorted((before - after) - gold),
                'new_no_answer_error': bool(left.get('no_answer') and not left['actual_ids'] and right['actual_ids']),
                'lost_required_ids': sorted((before - after) & set(left['required_ids']))})
        passes = (not any(row['forbidden_hits'] for rows in routes.values() for row in rows)
                  and not any(row['lost_relevant_facts'] or row['new_irrelevant_facts']
                              or row['lost_required_ids'] or row['new_no_answer_error'] for row in comparisons))
        datasets[name] = {'paired_queries': len(base), 'candidate_order_matches': True,
            'candidate_gate_passes': passes, 'comparisons': comparisons,
            'routes': {route: {'summary': summarize(rows), 'queries': rows} for route, rows in routes.items()}}
    return {'selection_uses': 'observed data only; no tuning or independent test claim',
            'datasets': datasets, 'candidate_gate_passes': all(d['candidate_gate_passes'] for d in datasets.values()),
            'status': 'measured'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('freeze', 'test', 'compare'), required=True)
    parser.add_argument('--frozen', type=Path)
    parser.add_argument('--protocol', type=Path, default=HERE / 'tuning-protocol.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError('output already exists')
    protocol = json.loads(args.protocol.read_text())
    hashes = manifest(args.protocol)
    fixture_name = protocol.get('fixture_name', 'tuning-fixtures.json')
    if hashes[fixture_name] != protocol['fixtures_sha256']:
        raise ValueError('fixtures differ from predeclared protocol')
    if hashes['fixtures.json'] != protocol['old_fixtures_sha256']:
        raise ValueError('old development fixtures changed')
    data = json.loads((HERE / fixture_name).read_text())
    if protocol.get('experiment') in ('robustness', 'dedup'):
        from .retrieval_robustness import select_results as robust_selector
        runner = partial(run_queries, selector=robust_selector)
    elif protocol.get('experiment') is None:
        runner = run_queries
    else:
        raise ValueError('unknown research experiment')
    if protocol.get('experiment') == 'dedup':
        if args.stage != 'compare':
            raise ValueError('dedup requires compare; its data is already observed')
        if any(hashes.get(name) != expected for name, expected in protocol['observed_datasets'].items()):
            raise ValueError('observed fixtures differ from comparison protocol')
    elif args.stage == 'compare':
        raise ValueError('compare requires dedup protocol')
    common = {'created_at': datetime.now(timezone.utc).isoformat(), 'input_hashes': hashes, 'runtime': runtime()}
    if args.stage == 'compare':
        result = {**common, **compare_observed(protocol, runner=runner)}
    elif args.stage == 'freeze':
        profiles = calibrate(data, protocol, runner=runner)
        validation = validate(data, protocol, profiles, runner=runner)
        result = {**common, 'profiles': profiles, 'validation': validation,
                  'test_queries_run': 0, 'status': 'frozen' if validation['selected_route'] else 'needs_revision'}
    else:
        if args.frozen is None:
            raise ValueError('test requires a frozen selection')
        frozen = json.loads(args.frozen.read_text())
        if frozen['input_hashes'] != hashes:
            raise ValueError('code/input changed since freeze; disclose reuse before repeating')
        result = {**common, 'frozen_sha256': digest(args.frozen), **evaluate(data, protocol, frozen, runner=runner)}
    save_new(args.output, result)
    selected = result.get('selected_route', result.get('validation', {}).get('selected_route'))
    print(json.dumps({'stage': args.stage, 'output': str(args.output), 'selected_route': selected,
                      'status': result.get('status', 'frozen')}, ensure_ascii=False))
    return 0 if result['status'] in ('frozen', 'measured') else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        print(f'memory_tuning_failed error_type={type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
