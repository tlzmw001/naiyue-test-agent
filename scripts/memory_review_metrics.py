"""Validate complete human comparison/output reviews; never infer semantic verdicts."""


def _boolean(value):
    if type(value) is not bool:raise ValueError('review_boolean_required')
    return value


def evaluate(report,rows):
    by_id={r['case_id']:r for r in rows}
    if len(by_id)!=len(rows) or set(by_id)!={c['case_id'] for c in report['cases']}:
        raise ValueError('review_coverage_invalid')
    backend=report.get('entry')=='background'
    if report.get('entry') not in ('background','foreground'):raise ValueError('review_entry_invalid')
    required=attempted=correct=old_required=old_found=duplicates=scope=time=total=0
    output_total=unsupported=time_total=0;unavailable=False
    for case in report['cases']:
        row=by_id[case['case_id']]
        facts={f['fact_id']:f for f in case['expected_facts']}
        ratings=row['facts']
        if len(ratings)!=len(facts) or {f['fact_id'] for f in ratings}!=set(facts):
            raise ValueError('fact_coverage_invalid')
        for rating in ratings:
            total+=1
            scope+=_boolean(rating.get('scope_correct'))
            if not (report.get('policy')=='baseline' and 'time_correct' in rating and rating['time_correct'] is None):
                time+=_boolean(rating.get('time_correct'));time_total+=1
            duplicates+=_boolean(rating.get('duplicate_added'))
            if backend:
                comparison=rating.get('comparison',{})
                done=_boolean(comparison.get('attempted'));required+=1;attempted+=done
                if facts[rating['fact_id']].get('target_old') is not None:
                    old_required+=1;old_found+=_boolean(comparison.get('old_recalled'))
                elif comparison.get('old_recalled') is not None:
                    raise ValueError('old_recall_not_applicable')
                if done:
                    index=comparison.get('output_index');outputs=case.get('model_outputs',[])
                    if type(index) is not int or not 0<=index<len(outputs) or outputs[index].get('stage')!='compare':
                        raise ValueError('comparison_output_reference_invalid')
                    relation=_boolean(comparison.get('relation_correct'))
                    target=_boolean(comparison.get('target_correct'))
                    correct+=relation and target
                elif any(comparison.get(k) is not None for k in ('output_index','relation_correct','target_correct')):
                    raise ValueError('unattempted_comparison_has_verdict')
        memories=case.get('memories')
        if memories is None:unavailable=True
        identities=['memory:'+str(m['scope'])+':'+str(m['memory_id'])+':'+str(m['version']) for m in memories or []]
        identities.extend('model_output:'+str(i) for i,_ in enumerate(case.get('model_outputs',[])))
        if (case.get('followup') or {}).get('answer') is not None:identities.append('followup')
        reviews=row.get('output_reviews',[])
        if len(set(identities))!=len(identities) or len(reviews)!=len(identities) or {v['id'] for v in reviews}!=set(identities):
            raise ValueError('output_review_coverage_invalid')
        for value in reviews:
            if not isinstance(value.get('reason'),str) or not value['reason'].strip():raise ValueError('output_review_basis_missing')
            unsupported+=not _boolean(value.get('supported'));output_total+=1
    ratio=lambda n,d:n/d if d else None
    return dict(comparison_metrics=dict(required=required,attempted=attempted,
        completion_rate=ratio(attempted,required),correct=correct,decision_accuracy=ratio(correct,attempted),
        old_required=old_required,old_found=old_found,old_recall=ratio(old_found,old_required)) if backend else None,
        fact_dimensions=dict(total=total,scope_correct=scope,time_correct=time,time_total=time_total),duplicate_added=duplicates,
        output_total=output_total,unsupported_outputs=unsupported,outputs_supported=unsupported==0,
        output_review_status='unavailable' if unavailable else 'complete')
