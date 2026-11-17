"""Calibrated budgets cover summary, candidate and state carry."""
import asyncio
from uuid import uuid4
import pytest
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from runtime_context_helpers import typed_context
from naiyue_test_agent.agent_runtime import context_calibration as calibration
from naiyue_test_agent.agent_runtime.context_compaction import _acompact_messages, ContextCompactionError
from test_context_calibration_runtime import identified, answer
from test_async_context_chain import model


def seeded(chosen,ratio=4):
    messages=[HumanMessage(content='old '*200),AIMessage(content='old'),HumanMessage(content='new')]
    measurement=calibration.measure_request(chosen,None,messages,[])
    record=calibration.complete_sample(measurement,answer(measurement.budget.raw_input_tokens*ratio),sample_id=uuid4().hex)
    messages[1].response_metadata[calibration.KEY]=record
    return messages,record


def test_summary_guard_uses_calibrated_input():
    chosen=identified(model(window=1000,output=20,reserve=900))
    messages,record=seeded(chosen)
    with pytest.raises(ContextCompactionError,match='summary_input_exceeds_budget'):
        asyncio.run(_acompact_messages(messages,chosen,model_id='A',context=typed_context(),system_messages=[],tools=[]))
    assert not chosen.summary_inputs and messages[1].response_metadata[calibration.KEY]==record


def test_compaction_carries_samples_and_summary_only_updates_own_slot(caplog):
    caplog.set_level("INFO")
    chosen=identified(model(window=4000,output=20,reserve=3650,summaries=[answer(300,'brief')]))
    messages,record=seeded(chosen,2)
    update=asyncio.run(_acompact_messages(messages,chosen,model_id='A',context=typed_context(),system_messages=[],tools=[]))
    carried=update['messages'][1].response_metadata[calibration.KEY]
    started=[r.context_compaction for r in caplog.records if r.message == 'context_summary_model_started']
    assert started[0]['method'] == 'inherited_agent_ratio'
    assert started[0]['invalidation_reason'] is None
    assert carried['agent']==record['agent']
    assert carried['summary']['text']['actual_input_tokens']==300
    assert carried['revision']>record['revision']
    assert messages[1].response_metadata[calibration.KEY]==record


@pytest.mark.parametrize('usage',[False,True])
def test_missing_summary_usage_preserves_agent_and_identity_optional(usage):
    chosen=identified(model(window=4000,output=20,reserve=3650,summaries=[answer(300,'brief') if usage else AIMessage(content='brief')]))
    messages,record=seeded(chosen,2)
    update=asyncio.run(_acompact_messages(messages,chosen,model_id='A',context=typed_context(),system_messages=[],tools=[]))
    carried=update['messages'][1].response_metadata[calibration.KEY]
    assert carried['agent']==record['agent']
    assert bool(carried['summary']['text'])==usage


def test_compaction_without_identity_stays_uncalibrated():
    chosen=model(window=4000,output=20,reserve=3900,keep=5)
    messages=[HumanMessage(content='old '*200),AIMessage(content='old'),HumanMessage(content='new')]
    update=asyncio.run(_acompact_messages(messages,chosen,model_id='A',context=typed_context(),system_messages=[],tools=[]))
    assert calibration.KEY not in update['messages'][1].response_metadata
    assert len(chosen.summary_inputs)==1


def test_final_candidate_guard_uses_calibrated_input(monkeypatch):
    chosen=identified(model(window=4000,output=20,reserve=3650,summaries=[AIMessage(content='large '*2000)]))
    messages,record=seeded(chosen,2)
    with pytest.raises(ContextCompactionError,match='compacted_request_over_budget'):
        asyncio.run(_acompact_messages(messages,chosen,model_id='A',context=typed_context(),system_messages=[],tools=[]))
    assert len(chosen.summary_inputs)==1
    assert messages[1].response_metadata[calibration.KEY]==record


def test_suffix_uses_text_slot_after_image_history():
    from naiyue_test_agent.agent_runtime.context_compaction import _cut_at_complete_turn
    chosen=identified(model());chosen.profile={'image_inputs':True}
    text=[HumanMessage(content='text')]
    measurement=calibration.measure_request(chosen,None,text,[])
    record=calibration.complete_sample(measurement,answer(measurement.budget.raw_input_tokens*2),sample_id=uuid4().hex)
    image=HumanMessage(content=[{'type':'image_url','image_url':{'url':'https://example.test/image'}}])
    measurement=calibration.measure_request(chosen,None,[image],[],calibration_history=[AIMessage(content='',response_metadata={calibration.KEY:record})])
    record=calibration.complete_sample(measurement,answer(measurement.budget.raw_input_tokens*20),sample_id=uuid4().hex)
    history=[image,AIMessage(content='',response_metadata={calibration.KEY:record}),HumanMessage(content='a'),AIMessage(content='b'),HumanMessage(content='c')]
    values=[]
    def suffix(messages):
        result=calibration.measure_request(chosen,None,messages,[],calibration_history=history)
        values.append(result.calibration_ratio)
        return result.budget.calibrated_input_tokens
    assert _cut_at_complete_turn(history,25,suffix)==2
    assert values==[2,2]


def test_sampling_failure_cannot_drop_agent_ratio_in_post_guard(monkeypatch):
    from naiyue_test_agent.agent_runtime import context_compaction
    chosen=identified(model(window=4000,output=20,reserve=3650,summaries=[AIMessage(content='large '*2000)]))
    messages,record=seeded(chosen,2)
    faults=[]
    def fail(*args,**kwargs):
        faults.append(True)
        raise ValueError('private')
    monkeypatch.setattr(context_compaction,'carry_compaction_state',fail)
    with pytest.raises(ContextCompactionError,match='compacted_request_over_budget'):
        asyncio.run(_acompact_messages(messages,chosen,model_id='A',context=typed_context(),system_messages=[],tools=[]))
    assert faults==[True] and messages[1].response_metadata[calibration.KEY]==record


def test_response_format_disables_old_ratio_in_compaction():
    chosen=identified(model(window=4000,output=20,reserve=3650))
    messages,record=seeded(chosen,2)
    result=asyncio.run(_acompact_messages(messages,chosen,model_id='A',context=typed_context(),
        system_messages=[],tools=[],response_format=object()))
    assert result is None and not chosen.summary_inputs
    assert messages[1].response_metadata[calibration.KEY]==record


@pytest.mark.parametrize('force', [False, True])
def test_existing_summary_only_initial_prefix_searches_later_cut(force):
    from naiyue_test_agent.agent_runtime.context_compaction import SUMMARY_MARKER, SUMMARY_PREFIX
    chosen = model(window=2000, output=1500, reserve=1500, keep=500,
                   summary_output=8, summaries=[AIMessage(content='brief')])
    messages = [
        HumanMessage(content=SUMMARY_PREFIX + 'previous',
                     additional_kwargs={SUMMARY_MARKER: True}),
        HumanMessage(content='x' * 4000),
        AIMessage(content='progress'),
        HumanMessage(content='new'),
    ]
    original = [message.model_copy(deep=True) for message in messages]
    update = asyncio.run(_acompact_messages(
        messages, chosen, model_id='A', context=typed_context(),
        system_messages=[], tools=[], force=force))
    assert len(chosen.summary_inputs) == 1
    prompt = chosen.summary_inputs[0][-1].content
    assert '<previous-summary>\nprevious\n</previous-summary>' in prompt
    assert '<turn-prefix>' in prompt and 'x' * 4000 in prompt
    assert update['messages'][1].additional_kwargs[SUMMARY_MARKER] is True
    assert update['messages'][2:] == messages[2:]
    assert messages == original


def test_anthropic_thinking_summary_is_visible_text():
    from anthropic.types import Message
    from langchain_anthropic import ChatAnthropic
    response = ChatAnthropic(model='fixture', api_key='synthetic')._format_output(Message(
        id='summary', type='message', role='assistant', model='fixture', stop_reason='end_turn',
        content=[{'type': 'thinking', 'thinking': 'PRIVATE', 'signature': 'fixture'},
                 {'type': 'text', 'text': 'brief'}],
        usage={'input_tokens': 300, 'output_tokens': 10})).generations[0].message
    chosen = identified(model(window=4000, output=20, reserve=3650, summaries=[response]))
    chosen.metadata['naiyue_model_input'] = {'kind': 'anthropic', 'api': None}
    messages, _ = seeded(chosen, 2)
    update = asyncio.run(_acompact_messages(messages, chosen, model_id='A', context=typed_context(),
        system_messages=[], tools=[], force=True, trigger='manual'))
    assert update['messages'][1].content.endswith('brief')
    assert 'PRIVATE' not in update['messages'][1].content
    assert len(chosen.summary_inputs) == 1
