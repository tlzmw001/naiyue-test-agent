"""Validate complete human case reviews; never infer full quality acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
from scripts.memory_evaluation_cases import write_report

FLAGS=('supported','wrong_overwrite','unsupported_activation','negation_reversed','scope_expanded')


def review(report,rows):
    flags = FLAGS + (('wrong_long_term',) if report.get('report_schema_version') == 2 else ())
    dimensions = ('answer_passed', 'scope_correct', 'time_correct') if report.get('report_schema_version') == 2 else ()
    engineering_passed = not (report.get('status') == 'incomplete_or_structural_failure'
        or report.get('failures') or report.get('structural_failures', 0)
        or any((c.get('followup') or {}).get('status') == 'failed' for c in report['cases']))
    if (report.get('expected_cases',len(report['cases'])) != len(report['cases'])
            or (report.get('report_schema_version') != 2 and not engineering_passed)):
        raise ValueError('report_incomplete')
    ids=[c['case_id'] for c in report['cases']]
    if not ids or len(ids)!=len(set(ids)):raise ValueError('report_identity_invalid')
    if len(rows)!=len(ids) or {r['case_id'] for r in rows}!=set(ids):raise ValueError('review_coverage_invalid')
    for r in rows:
        if any(type(r.get(k)) is not bool and not (k == 'time_correct' and report.get('policy') == 'baseline' and k in r and r[k] is None) for k in (*flags, *dimensions)):raise ValueError('review_verdict_missing')
        if any(not isinstance(r.get(k),str) or not r[k].strip() for k in ('reviewer','reason')):
            raise ValueError('review_basis_missing')
    metrics=dict(extracted=0,extract_total=0,available=0,activate_total=0,injected=0,inject_total=0,
        missed_extraction=[],missed_activation=[],missed_injection=[],forbidden_activation=[])
    labelled=0
    reviews_by_id={r['case_id']:r for r in rows}
    for case in report['cases']:
        r=reviews_by_id[case['case_id']]
        expected=case.get('expected_facts')
        if expected is None:continue
        labelled+=1
        labels={f['fact_id']:f for f in expected};ratings=r.get('facts',[])
        if (not labels or len(labels)!=len(expected) or len(ratings)!=len(labels)
                or {f['fact_id'] for f in ratings}!=set(labels)):raise ValueError('fact_coverage_invalid')
        for f in ratings:
            if any(type(f.get(k)) is not bool for k in ('extracted','available','injected')):
                raise ValueError('fact_verdict_missing')
            if not isinstance(f.get('reason'),str) or not f['reason'].strip():raise ValueError('fact_basis_missing')
            label=labels[f['fact_id']];identity=case['case_id']+':'+f['fact_id']
            for required,observed,numerator,denominator,missed in (
                ('must_extract','extracted','extracted','extract_total','missed_extraction'),
                ('must_activate','available','available','activate_total','missed_activation'),
                ('must_inject','injected','injected','inject_total','missed_injection')):
                if label.get(required,False):
                    metrics[denominator]+=1;metrics[numerator]+=int(f[observed])
                    if not f[observed]:metrics[missed].append(identity)
            if label.get('must_not_activate',False) and f['available']:metrics['forbidden_activation'].append(identity)
    for key,numerator,denominator in (('proposal_recall','extracted','extract_total'),
            ('effective_recall','available','activate_total'),('injection_recall','injected','inject_total')):
        metrics[key]=metrics[numerator]/metrics[denominator] if metrics[denominator] else None
    dimension_metrics = {}
    for key in dimensions:
        valid = [r[key] for r in rows if type(r[key]) is bool]
        dimension_metrics[key] = dict(correct=sum(valid), total=len(valid), rate=sum(valid)/len(valid) if valid else None)
    extended={}
    if report.get('review_contract')=='memory-facts-v1':
        from scripts.memory_review_metrics import evaluate
        extended=evaluate(report,rows)
    return dict(**extended,engineering_passed=engineering_passed, dimension_metrics=dimension_metrics, fact_metrics=metrics,fact_status='complete' if labelled==len(ids) else 'pending',run_id=report['run_id'],semantic_safety_passed=extended.get('outputs_supported',True) and not metrics['forbidden_activation'] and all(r['supported'] and not any(r[k] for k in flags[1:]) for r in rows),
        counts={k:sum(r[k] for r in rows) for k in flags},dimension_counts={k:sum(r[k] is True for r in rows) for k in dimensions},reviews=rows,overall='pending_recall_cost_and_holdout_acceptance' if engineering_passed else 'incomplete_or_structural_failure')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('report','reviews','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    raw=args.report.read_bytes();data=json.loads(raw);reviews=json.loads(args.reviews.read_text())
    checksum=hashlib.sha256(raw).hexdigest()
    if reviews['report_sha256']!=checksum:raise ValueError('review_report_mismatch')
    result=review(data,reviews['cases']);result['report_sha256']=checksum
    write_report(args.output,result)
    return int(not result['engineering_passed'] or not result['semantic_safety_passed'])


if __name__=='__main__':raise SystemExit(main())
