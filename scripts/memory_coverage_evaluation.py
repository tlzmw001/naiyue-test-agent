"""Execute and account for fixed legacy cases and current engineering matrices."""
import argparse
import hashlib
import json
from pathlib import Path
from scripts.memory_evaluation_cases import ROOT,load_suite,load_engineering_matrix,write_report


def evaluate():
    from scripts.memory_evaluation_runner import execute_case
    from scripts.memory_worker_evaluation import evaluate as worker
    from scripts.memory_recall_evaluation import evaluate as recall
    from scripts.evaluate_memory_optimization import evidence_manifest
    legacy,protocol=load_suite();rows=[]
    matrices={}
    for split in ('calibration','validation','holdout'):
        matrices[split]=dict(worker=worker(split),recall=recall(split))
    for case in legacy:
        suite=case['suite'];split=case['split']
        result=execute_case(case) if suite!='semantic' else None
        replacements=[]
        if suite=='comparison':
            for engineering_split in ('calibration','validation','holdout'):
                cases,_,_=load_engineering_matrix('worker',engineering_split)
                replacements.extend(engineering_split+':'+c['id'] for c in cases if c['relation'] in case['expected_relations'])
        elif suite=='recall':replacements=[r['case_id'] for r in matrices[split]['recall']['cases']]
        rows.append(dict(case_id=case['case_id'],family_id=case['family_id'],suite=suite,split=split,
            requirement=case['assertion'],expected_relations=case['expected_relations'],
            entrypoint=result['entrypoint'] if result else 'current space-time protocol; provider not requested',
            status='superseded_type_contract' if suite=='semantic' else 'executed',
            observed=result,engineering_replacements=replacements,
            replacement_scope='Relation-level mechanical coverage only; not same semantic problem family',
            limitation=('Controlled public write, not model comparison quality' if suite=='comparison' else
                'Legacy seven-kind semantics superseded; independent two-kind quality still pending' if suite=='semantic' else
                'Exact source reconstruction and evidence coverage' if suite=='input' else
                'Single-target lexical regression; multi-item matrix is separate')))
    modern_path=ROOT/'tests/data/memory_space_time'
    modern,modern_protocol=load_suite(modern_path/'fixtures.json',modern_path/'protocol.json')
    engineering_passed=all(r['observed']['passed'] for r in rows if r['suite'] in ('input','recall')) and all(c['passed'] for m in matrices.values() for r in m.values() for c in r['cases'])
    return dict(format=1,synthetic=True,provider_calls=0,manifest=evidence_manifest(),
        legacy_fixtures_sha256=protocol['suite_fixtures_sha256'],
        modern_fixtures_sha256=modern_protocol['suite_fixtures_sha256'],legacy_cases=rows,
        modern_cases=[dict(case_id=c['case_id'],split=c['split'],status='not_sampled_needs_human_review',
            expected_fact_ids=[f['fact_id'] for f in c['expected_facts']]) for c in modern],
        matrices=matrices,engineering_matrices_passed=engineering_passed,
        legacy_expectation_mismatches=[c['case_id'] for c in rows if c['observed'] and c['observed']['passed'] is False],
        semantic_status='pending_real_comparison_and_human_review',
        scope='Case inventory and explicit execution/retirement, not semantic equivalence or product quality acceptance')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or args.output.is_symlink():parser.error('output must not exist')
    report=evaluate();write_report(args.output,report)
    print(json.dumps(dict(legacy_cases=len(report['legacy_cases']),modern_cases=len(report['modern_cases']),
        engineering_matrices_passed=report['engineering_matrices_passed'],legacy_mismatches=report['legacy_expectation_mismatches'],provider_calls=0)))
    return int(not report['engineering_matrices_passed'])


if __name__=='__main__':raise SystemExit(main())
