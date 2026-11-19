import pytest


def sample(entry='background'):
    case=dict(case_id='c',expected_facts=[dict(fact_id='f',target_old='old')],
        model_outputs=[dict(stage='compare')],memories=[dict(scope='workspace',memory_id='a',version=1)],
        followup=dict(answer='answer'))
    fact=dict(fact_id='f',scope_correct=True,time_correct=True,duplicate_added=False,
        comparison=dict(attempted=False,old_recalled=False,relation_correct=None,target_correct=None,output_index=None))
    outputs=[dict(id='memory:workspace:a:1',supported=True,reason='source'),dict(id='followup',supported=True,reason='source'),dict(id='model_output:0',supported=True,reason='source')]
    return dict(entry=entry,cases=[case]),[dict(case_id='c',facts=[fact],output_reviews=outputs)]


def test_missing_comparisons_stay_in_denominator_and_frontend_is_na():
    from scripts.memory_review_metrics import evaluate
    report,ratings=sample();r=evaluate(report,ratings)
    assert r['comparison_metrics']['required']==1 and r['comparison_metrics']['attempted']==0
    assert r['comparison_metrics']['completion_rate']==0
    assert r['comparison_metrics']['old_recall']==0
    assert r['comparison_metrics']['decision_accuracy'] is None
    report['entry']='foreground'
    assert evaluate(report,ratings)['comparison_metrics'] is None


@pytest.mark.parametrize('fault',['output','dimension','index','boolean'])
def test_incomplete_reviews_are_rejected(fault):
    from scripts.memory_review_metrics import evaluate
    report,ratings=sample();fact=ratings[0]['facts'][0]
    if fault=='output':ratings[0]['output_reviews'].pop()
    if fault=='dimension':fact.pop('scope_correct')
    if fault=='index':fact['comparison'].update(attempted=True,output_index=2,relation_correct=True,target_correct=True)
    if fault=='boolean':fact['comparison']['old_recalled']='false'
    with pytest.raises(ValueError):evaluate(report,ratings)


def test_extra_unsupported_output_and_duplicate_are_counted():
    from scripts.memory_review_metrics import evaluate
    report,ratings=sample();ratings[0]['output_reviews'][0]['supported']=False
    ratings[0]['facts'][0]['duplicate_added']=True
    result=evaluate(report,ratings)
    assert not result['outputs_supported'] and result['duplicate_added']==1


def test_unknown_snapshot_does_not_claim_complete_output_review():
    from scripts.memory_review_metrics import evaluate
    report,ratings=sample();report['cases'][0]['memories']=None
    ratings[0]['output_reviews']=ratings[0]['output_reviews'][1:]
    assert evaluate(report,ratings)['output_review_status']=='unavailable'


def test_new_contract_is_wired_to_review_safety_and_legacy_unchanged():
    from scripts.review_memory_evaluation import review
    report,ratings=sample('foreground')
    report.update(review_contract='memory-facts-v1',run_id='r',report_schema_version=2)
    ratings[0].update(reviewer='human',reason='checked',supported=True,wrong_overwrite=False,
        unsupported_activation=False,negation_reversed=False,scope_expanded=False,wrong_long_term=False,
        answer_passed=True,scope_correct=True,time_correct=True)
    ratings[0]['facts'][0].update(extracted=True,available=True,injected=False,reason='checked')
    ratings[0]['output_reviews'][0]['supported']=False
    assert not review(report,ratings)['semantic_safety_passed']
    report.pop('review_contract')
    assert review(report,ratings)['semantic_safety_passed']


def test_baseline_without_time_classification_keeps_unknown_out_of_time_denominator():
    from scripts.memory_review_metrics import evaluate
    report,ratings=sample();report['policy']='baseline'
    ratings[0]['facts'][0]['time_correct']=None
    result=evaluate(report,ratings)
    assert result['fact_dimensions']['time_total']==0
    report['policy']='candidate'
    with pytest.raises(ValueError):evaluate(report,ratings)
