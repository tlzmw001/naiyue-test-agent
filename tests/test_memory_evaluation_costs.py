import pytest


def test_partial_usage_and_failed_call_are_not_reported_as_zero():
    from scripts.memory_evaluation_costs import CaseCosts
    clock=iter([1.,2.,3.,5.,7.,8.])
    cost=CaseCosts(clock=lambda:next(clock))
    cost.start('a','extract');cost.finish('a',{'input_tokens':10,'output_tokens':2})
    cost.start('b','extract');cost.finish('b',None,failed=True)
    result=cost.snapshot()
    stage=result['models']['extract']
    assert stage['logical_calls']==2 and stage['failed']==1
    assert stage['input_tokens'] is None and stage['known_input_tokens']==10
    assert stage['unknown_usage_calls']==1 and stage['model_ms']==3000
    assert result['models']['compare']['logical_calls']==0
    assert result['sdk_attempts'] is None


def test_read_observation_restores_original_method_and_exception():
    from scripts.memory_evaluation_costs import CaseCosts
    class Memory:
        def query(self,sid,project,payload):raise OSError('private')
        def comparison_candidates(self,*args,**kwargs):return {'rows':[]}
    memory=Memory();original=memory.query;cost=CaseCosts()
    restore=cost.observe(memory)
    with pytest.raises(OSError):memory.query('s','p',{'action':'read'})
    memory.comparison_candidates('s','p',plan={'queries':[{},{}]})
    restore()
    assert memory.query==original
    result=cost.snapshot()
    assert result['reads']['read']['calls']==1 and result['reads']['read']['failed']==1
    assert result['comparison_reads']['planned_queries']==2
    assert 'private' not in str(result)
