"""Request-local calibration contracts; no model requests."""
from copy import deepcopy
from fractions import Fraction
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

KEY = 'naiyue_token_calibration'


def api():
    from naiyue_test_agent.agent_runtime import context_calibration
    return context_calibration


def model():
    return SimpleNamespace(metadata={'naiyue_context_estimation': {'version': 1, 'route_fingerprint': 'a'*64}})


def response(actual=4000, name=None):
    return AIMessage(content='ok', usage_metadata={'input_tokens': actual, 'output_tokens': 1, 'total_tokens': actual+1},
                     response_metadata={'model_name': name} if name else {})


def measure(monkeypatch, raw=1000, history=(), **kwargs):
    m=api()
    monkeypatch.setattr(m, 'count_tokens_approximately', lambda *a, **k: raw)
    return m.measure_request(model(), None, [HumanMessage(content='hello')], [], calibration_history=history, **kwargs)


def attached(record):
    return AIMessage(content='old', response_metadata={KEY: record})


def test_latest_ratio_updates_both_directions(monkeypatch):
    m=api(); first=measure(monkeypatch)
    assert first.budget.calibrated_input_tokens == 1000
    record=m.complete_sample(first,response(),sample_id=uuid4().hex)
    second=measure(monkeypatch,1500,[attached(record)])
    assert second.budget.calibrated_input_tokens == 6000
    record=m.complete_sample(second,response(5400),sample_id=uuid4().hex)
    third=measure(monkeypatch,2000,[attached(record)])
    assert third.calibration_ratio == Fraction(18,5)
    assert third.budget.calibrated_input_tokens == 7200
    record=m.complete_sample(third,response(1000),sample_id=uuid4().hex)
    assert measure(monkeypatch,3000,[attached(record)]).budget.calibrated_input_tokens == 1500


def test_bad_high_revision_recovers_and_carry_wins(monkeypatch):
    m=api(); initial=measure(monkeypatch)
    good=m.complete_sample(initial,response(),sample_id=uuid4().hex)
    bad=deepcopy(good);bad['revision']=10;bad['agent']['text']['raw_input_tokens']=True
    current=measure(monkeypatch,1000,[attached(good),attached(bad)])
    assert current.budget.invalidation_reason == 'invalid_record'
    repaired=m.complete_sample(current,response(3000),sample_id=uuid4().hex)
    assert repaired['revision']==11
    carried=m.carry_compaction_state(measure(monkeypatch,1000,[attached(repaired)]),None)
    assert carried['revision']==12
    assert measure(monkeypatch,2000,[attached(carried),attached(good),attached(bad)]).budget.calibrated_input_tokens==6000


def test_summary_inheritance_missing_usage_and_model_change(monkeypatch):
    m=api();first=measure(monkeypatch)
    record=m.complete_sample(first,response(name='v1'),sample_id=uuid4().hex)
    summary=measure(monkeypatch,500,[attached(record)],purpose='summary')
    assert summary.budget.method=='inherited_agent_ratio'
    assert summary.budget.calibrated_input_tokens==2000
    assert m.complete_sample(summary,AIMessage(content='no usage'),sample_id=uuid4().hex) is None
    changed=m.complete_sample(summary,response(1000,'v2'),sample_id=uuid4().hex)
    assert changed['agent']==record['agent']
    assert changed['summary']['text']['actual_input_tokens']==1000


@pytest.mark.parametrize('field,value',[('version',True),('revision',0),('identity','secret'),('extra',1)])
def test_strict_record_rejection(monkeypatch,field,value):
    m=api(); record=m.complete_sample(measure(monkeypatch),response(),sample_id=uuid4().hex)
    record[field]=value
    assert m.read_calibration(record) is None


def test_unsupported_shape_and_unavailable(monkeypatch):
    m=api();unsupported=measure(monkeypatch,response_format=object())
    assert not unsupported.sampling_eligible
    assert unsupported.budget.invalidation_reason=='unsupported_request_shape'
    monkeypatch.setattr(m,'count_tokens_approximately',lambda *a,**k: (_ for _ in ()).throw(ValueError('private')))
    unavailable=m.measure_request(model(),None,[],[])
    assert unavailable.calibration_ratio is None
    assert unavailable.budget.calibrated_input_tokens is None


def test_idempotence_conflict_and_no_mutation(monkeypatch):
    m=api();initial=measure(monkeypatch);sid=uuid4().hex
    record=m.complete_sample(initial,response(),sample_id=sid);before=deepcopy(record)
    next_call=measure(monkeypatch,1000,[attached(record)])
    assert m.complete_sample(next_call,response(),sample_id=sid)==record
    assert m.complete_sample(next_call,response(3000),sample_id=sid) is None
    assert record==before


def test_media_slots_model_change_and_serialization(monkeypatch):
    from langchain_core.messages import messages_from_dict, messages_to_dict
    m=api();text=measure(monkeypatch)
    record=m.complete_sample(text,response(4000,'v1'),sample_id=uuid4().hex)
    image=[HumanMessage(content=[{'type':'image_url','image_url':{'url':'data:image/png;base64,fixture'}}])]
    image_call=m.measure_request(model(),None,image,[],calibration_history=[attached(record)])
    assert image_call.budget.invalidation_reason=='media_changed'
    record=m.complete_sample(image_call,response(8000,'v1'),sample_id=uuid4().hex)
    assert record['agent']['text'] and record['agent']['image']
    restored=messages_from_dict(messages_to_dict([attached(record)]))
    assert measure(monkeypatch,2000,restored).budget.calibrated_input_tokens==8000
    changed=m.complete_sample(measure(monkeypatch,1000,restored),response(3000,'v2'),sample_id=uuid4().hex)
    assert changed['agent']['image'] is None
    assert changed['agent']['text']['actual_input_tokens']==3000


def test_latest_conflict_carry_identity_and_identity_unavailable(monkeypatch):
    m=api();call=measure(monkeypatch)
    record=m.complete_sample(call,response(),sample_id=uuid4().hex)
    conflict=deepcopy(record);conflict['agent']['text']['actual_input_tokens']=1
    assert measure(monkeypatch,1000,[attached(record),attached(conflict)]).budget.invalidation_reason=='invalid_record'
    other=deepcopy(record);other['identity']='b'*64
    assert m.carry_compaction_state(call,other) is None
    unknown=m.measure_request(SimpleNamespace(metadata={}),None,[],[])
    assert unknown.budget.invalidation_reason=='identity_unavailable'
    assert m.carry_compaction_state(unknown,None) is None


@pytest.mark.parametrize('actual',[0,-1,True,'4000'])
def test_invalid_usage_does_not_create_sample(monkeypatch,actual):
    message=AIMessage(content='bad')
    message.usage_metadata={'input_tokens':actual}
    assert api().complete_sample(measure(monkeypatch),message,sample_id=uuid4().hex) is None


def test_exact_large_rounding_and_size_limit(monkeypatch):
    m=api();first=measure(monkeypatch,3)
    record=m.complete_sample(first,response(4),sample_id=uuid4().hex)
    assert measure(monkeypatch,10**30+1,[attached(record)]).budget.calibrated_input_tokens==((10**30+1)*4+2)//3
    oversized=deepcopy(record);oversized['extra']='x'*16384
    assert m.read_calibration(oversized) is None


@pytest.mark.parametrize('provider', ['anthropic', 'responses'])
def test_native_tool_history_keeps_calibration(monkeypatch, provider):
    from langchain_core.messages import ToolMessage
    from naiyue_test_agent.model_access.messages import prepare_model_messages
    m=api(); initial=measure(monkeypatch)
    record=m.complete_sample(initial,response(),sample_id=uuid4().hex)
    if provider=='anthropic':
        from anthropic.types import Message
        from langchain_anthropic import ChatAnthropic
        adapter=ChatAnthropic(model='fixture',api_key='synthetic')
        native=Message(id='msg_fixture',type='message',role='assistant',model='fixture',
            content=[{'type':'tool_use','id':'call_fixture','name':'read_logs','input':{}}],
            stop_reason='tool_use',stop_sequence=None,usage={'input_tokens':100,'output_tokens':10})
        ai=adapter._format_output(native).generations[0].message
    else:
        from openai.types.responses import Response
        from langchain_openai import ChatOpenAI
        from langchain_openai.chat_models.base import _construct_lc_result_from_responses_api
        adapter=ChatOpenAI(model='fixture',api_key='synthetic',use_responses_api=True)
        native=Response.model_validate(dict(id='resp_fixture',created_at=1,object='response',
            model='fixture',parallel_tool_calls=True,tool_choice='auto',tools=[],status='completed',
            output=[dict(type='function_call',id='fc_fixture',call_id='call_fixture',
                name='read_logs',arguments='{}',status='completed')],usage=None))
        ai=_construct_lc_result_from_responses_api(native).generations[0].message
    adapter.metadata=model().metadata
    history=[HumanMessage(content='Read logs'),ai,ToolMessage(content='logs',tool_call_id='call_fixture')]
    projected=prepare_model_messages(history,adapter)
    measured=m.measure_request(adapter,None,projected,[],calibration_history=[attached(record)])
    assert measured.budget.method=='ratio_estimate'
    assert measured.budget.calibrated_input_tokens==4000
    assert measured.sampling_eligible
    updated=m.complete_sample(measured,response(3000),sample_id=uuid4().hex)
    assert updated['agent']['text']['actual_input_tokens']==3000


@pytest.mark.parametrize('kind', ['image_url','file'])
def test_system_content_participates_in_shape(monkeypatch,kind):
    from langchain_core.messages import SystemMessage
    m=api(); record=m.complete_sample(measure(monkeypatch),response(),sample_id=uuid4().hex)
    system=SystemMessage(content=[{'type':kind,'image_url':{'url':'https://example.test/image.png'}}])
    measured=m.measure_request(model(),system,[HumanMessage(content='hello')],[],calibration_history=[attached(record)])
    if kind=='image_url':
        assert measured.media=='image'
        assert measured.budget.invalidation_reason=='media_changed'
        assert measured.sampling_eligible
    else:
        assert measured.budget.invalidation_reason=='unsupported_request_shape'
        assert not measured.sampling_eligible
    assert measured.budget.calibrated_input_tokens==1000


@pytest.mark.parametrize('kind', ['server_tool_use','web_search_call','unknown'])
def test_server_and_unknown_blocks_remain_unsupported(monkeypatch,kind):
    m=api(); measure(monkeypatch)
    measured=m.measure_request(model(),None,[AIMessage(content=[{'type':kind}])],[])
    assert measured.budget.invalidation_reason=='unsupported_request_shape'
    assert not measured.sampling_eligible
