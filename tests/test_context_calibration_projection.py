from copy import deepcopy
from uuid import uuid4
import pytest
from langchain_core.messages import HumanMessage, AIMessage
from langchain.agents.middleware import ModelResponse
from runtime_context_helpers import typed_context, ModelCallState
from test_async_context_chain import model
from test_context_calibration_runtime import identified, answer
from naiyue_test_agent.agent_runtime.context_calibration import measure_request, complete_sample, KEY
from naiyue_test_agent.agent_runtime.context_usage import estimate_context, request_snapshot, finish_snapshot, read_snapshot


def snapshot(ratio=2):
    chosen=identified(model()); messages=[HumanMessage(content='hello')]
    measured=measure_request(chosen,None,messages,[])
    record=complete_sample(measured,answer(measured.budget.raw_input_tokens*ratio),sample_id=uuid4().hex)
    messages.append(AIMessage(content='prior',response_metadata={KEY:record}))
    measured=measure_request(chosen,None,messages,[])
    estimate=estimate_context(chosen,None,messages,[],measurement=measured)
    return request_snapshot(typed_context(model_call=ModelCallState('call','A')),estimate)


def test_v2_preserves_raw_groups_and_frozen_prediction():
    value=snapshot(); assert value['version']==2
    estimate=value['estimate']; raw=estimate['estimated_input_tokens']
    assert estimate['calibration']['calibrated_input_tokens']==raw*2
    assert sum(g['tokens'] for g in estimate['groups'])==raw
    finished=finish_snapshot(value,ModelResponse(result=[answer(raw*3)]))
    assert finished['estimate']==estimate
    assert finished['usage']['input_tokens']==raw*3
    assert read_snapshot(finished)==finished


@pytest.mark.parametrize('bad',[True,-1,'10',float('nan'),None])
def test_v2_rejects_invalid_calibration(bad):
    value=snapshot();value['estimate']['calibration']['calibrated_input_tokens']=bad
    assert read_snapshot(value) is None


def test_legacy_snapshot_remains_legacy():
    value=snapshot();value['version']=1;value['estimate'].pop('calibration')
    assert read_snapshot(value)==value
    value['estimate']['calibration']={}
    assert read_snapshot(value) is None


def test_downward_ratio_is_accepted():
    value=snapshot();value['estimate']['calibration']['calibrated_input_tokens']=1
    assert read_snapshot(value)==value


def test_group_failure_retains_measured_totals(monkeypatch):
    from naiyue_test_agent.agent_runtime import context_usage
    chosen=identified(model()); messages=[HumanMessage(content='hello')]
    measurement=measure_request(chosen,None,messages,[])
    def fail(*args,**kwargs): raise ValueError('group failure')
    monkeypatch.setattr(context_usage,'count_tokens_approximately',fail)
    estimate=estimate_context(chosen,None,messages,[],measurement=measurement)
    assert estimate['groups']==[] and estimate['groups_status']=='unavailable'
    assert estimate['estimated_input_tokens']==measurement.budget.raw_input_tokens
    assert estimate['calibration']['calibrated_input_tokens']==measurement.budget.calibrated_input_tokens


def test_real_wrappers_preserve_sent_prediction_after_usage():
    import asyncio
    from types import SimpleNamespace
    from langchain.agents.middleware import ModelRequest
    from naiyue_test_agent.agent_runtime.model_input import ModelInputMiddleware
    from naiyue_test_agent.agent_runtime.context_observation import observe_model_request
    from naiyue_test_agent.agent_runtime.context_usage import SNAPSHOT_KEY
    async def run():
        chosen=identified(model());context=typed_context(model_call=ModelCallState('call','A'))
        request=ModelRequest(model=chosen,messages=[HumanMessage(content='hello')],tools=[],runtime=SimpleNamespace(context=context))
        async def returned(req):return ModelResponse(result=[answer(300)])
        async def observed(req):return await observe_model_request.awrap_model_call(req,returned)
        response=await ModelInputMiddleware().awrap_model_call(request,observed)
        message=response.result[0]; snapshot=message.response_metadata[SNAPSHOT_KEY]
        assert snapshot['version']==2 and snapshot['usage']['input_tokens']==300
        assert snapshot['estimate']['calibration']['calibrated_input_tokens']==context.invocation.model_call.context_measurement.budget.calibrated_input_tokens
        assert snapshot['estimate']['calibration']['method']=='uncalibrated'
        assert message.response_metadata[KEY]['agent']['text']['actual_input_tokens']==300
    asyncio.run(run())


@pytest.mark.parametrize('version', [1, 2])
def test_missing_cache_write_is_only_legacy_compatible(version):
    value = snapshot()
    value.update(version=version, status='returned', usage={'input_tokens': 10, 'output_tokens': 2, 'cache_read_tokens': 0})
    if version == 1:
        value['estimate'].pop('calibration')
        assert read_snapshot(value)['usage']['cache_write_tokens'] is None
    else:
        assert read_snapshot(value) is None
