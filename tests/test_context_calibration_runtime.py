"""Actual asynchronous wrappers pair each sent request with its returned usage."""
import asyncio
import logging
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from runtime_context_helpers import ModelCallState, typed_context
from naiyue_test_agent.agent_runtime.model_input import ModelInputMiddleware
from naiyue_test_agent.agent_runtime import context_calibration as calibration
from naiyue_test_agent.agent_runtime.context_observation import observe_model_request
from test_async_context_chain import model, graph_for, execute, history, overflow_error


def identified(chosen):
    chosen.metadata={**chosen.metadata,'naiyue_context_estimation':{'version':1,'route_fingerprint':'a'*64}}
    return chosen


def answer(actual, text='answer'):
    return AIMessage(content=text,usage_metadata={'input_tokens':actual,'output_tokens':1,'total_tokens':actual+1},
                     response_metadata={'finish_reason':'stop'})


def test_returned_usage_calibrates_next_preflight(monkeypatch,caplog):
    monkeypatch.setattr(calibration,'count_tokens_approximately',lambda *a,**k:1000)
    chosen=identified(model(window=10000,reserve=7000))
    context=typed_context(model_call=ModelCallState('call','A'))
    middleware=ModelInputMiddleware()
    async def run():
        request=ModelRequest(model=chosen,messages=[HumanMessage(content='hello')],tools=[],runtime=SimpleNamespace(context=context))
        async def returned(_):return ModelResponse(result=[answer(4000)])
        async def observed(req):return await observe_model_request.awrap_model_call(req,returned)
        first=await middleware.awrap_model_call(request,observed)
        assert calibration.KEY in first.result[0].response_metadata
        await middleware.awrap_model_call(request.override(messages=[*request.messages,*first.result]),observed)
    with caplog.at_level(logging.INFO):asyncio.run(run())
    preflight=[r.model_context_preflight for r in caplog.records if hasattr(r,'model_context_preflight')]
    assert [p['calibrated_input_tokens'] for p in preflight]==[1000,4000]
    assert [p['needs_compaction'] for p in preflight]==[False,True]
    assert [p['method'] for p in preflight]==['uncalibrated','ratio_estimate']
    assert [p['invalidation_reason'] for p in preflight]==['no_samples',None]


def test_async_graph_attaches_sample_and_overflow_pairs_retry():
    async def run():
        chosen=identified(model(window=2000,reserve=20,answers=[overflow_error(),answer(320)],summaries=[answer(200,'brief')]))
        result,_=await execute(graph_for({'A':chosen}),history(),'invoke')
        record=result['messages'][-1].response_metadata[calibration.KEY]
        sample=record['agent']['text']; summary=record['summary']['text']
        assert sample['actual_input_tokens']==320 and summary['actual_input_tokens']==200
        from langchain_core.messages.utils import count_tokens_approximately
        assert sample['raw_input_tokens']==count_tokens_approximately(chosen.normal_inputs[1])
        assert sample['sample_id']!=summary['sample_id']
        assert len(chosen.normal_inputs)==2 and len(chosen.summary_inputs)==1
    asyncio.run(run())


def test_observation_failure_still_samples(monkeypatch):
    from naiyue_test_agent.agent_runtime import context_observation
    monkeypatch.setattr(context_observation,'_publish_snapshot',lambda *a: (_ for _ in ()).throw(RuntimeError('private')))
    async def run():
        chosen=identified(model());context=typed_context(model_call=ModelCallState('call','A'))
        request=ModelRequest(model=chosen,messages=[HumanMessage(content='hello')],tools=[],runtime=SimpleNamespace(context=context))
        async def returned(_):return ModelResponse(result=[answer(100)])
        async def observed(req):return await observe_model_request.awrap_model_call(req,returned)
        response=await ModelInputMiddleware().awrap_model_call(request,observed)
        assert response.result[0].response_metadata[calibration.KEY]['agent']['text']['actual_input_tokens']==100
    asyncio.run(run())


@pytest.mark.parametrize('actual',[4500,800])
def test_latest_ratio_updates_both_directions_and_sent_measurement_frozen(monkeypatch,actual):
    monkeypatch.setattr(calibration,'count_tokens_approximately',lambda *a,**k:1000)
    async def run():
        context=typed_context(model_call=ModelCallState('call','A'))
        request=ModelRequest(model=identified(model()),messages=[HumanMessage(content='hello')],tools=[],runtime=SimpleNamespace(context=context))
        middleware=ModelInputMiddleware()
        async def first(_):return ModelResponse(result=[answer(3600)])
        response=await middleware.awrap_model_call(request,first)
        request=request.override(messages=[*request.messages,*response.result])
        captured=[]
        async def second(_):
            captured.append(context.invocation.model_call.context_measurement)
            return ModelResponse(result=[answer(actual)])
        response=await middleware.awrap_model_call(request,second)
        assert captured[0].budget.calibrated_input_tokens==3600
        next_call=calibration.measure_request(request.model,None,[*request.messages,*response.result],[])
        assert next_call.budget.calibrated_input_tokens==actual
    asyncio.run(run())


@pytest.mark.parametrize('failure',[RuntimeError('provider'),asyncio.CancelledError()])
def test_failed_or_cancelled_request_does_not_mutate_history(failure):
    async def run():
        message=HumanMessage(content='hello')
        request=ModelRequest(model=identified(model()),messages=[message],tools=[],runtime=SimpleNamespace(context=None))
        async def fail(_):raise failure
        with pytest.raises(type(failure)) as caught:
            await ModelInputMiddleware().awrap_model_call(request,fail)
        assert caught.value is failure and not message.response_metadata
    asyncio.run(run())


def test_response_format_disables_sampling_and_recovery_passes_format():
    from naiyue_test_agent.agent_runtime.context_recovery import _begin_recovery
    async def run():
        context=typed_context(model_call=ModelCallState('call','A'))
        request=ModelRequest(model=identified(model()),messages=[HumanMessage(content='hello')],tools=[],runtime=SimpleNamespace(context=context),response_format=object())
        async def returned(_):return ModelResponse(result=[answer(100)])
        response=await ModelInputMiddleware().awrap_model_call(request,returned)
        assert calibration.KEY not in response.result[0].response_metadata
        assert context.invocation.model_call.context_measurement.budget.invalidation_reason=='unsupported_request_shape'
        arguments,*_=_begin_recovery(request,'context_length_exceeded')
        assert arguments['response_format'] is request.response_format
    asyncio.run(run())


def test_sdk_payload_omits_calibration_metadata():
    import json
    import httpx
    from naiyue_test_agent.model_access import catalog_from_configuration
    from naiyue_test_agent.model_access.configuration import parse_model_configuration
    parsed=parse_model_configuration({'schema_version':1,'default_model_id':'m',
        'providers':{'p':{'kind':'openai_compatible','api_key':'synthetic','base_url':'https://example.test/v1'}},
        'models':{'m':{'provider_id':'p','model':'fixture','api':'chat_completions'}}})
    async def run():
        catalog=catalog_from_configuration(parsed);chosen=catalog.get('m');sent=[]
        def serve(req):
            sent.append(json.loads(req.content))
            return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':1,'model':'fixture',
                'choices':[{'index':0,'message':{'role':'assistant','content':'answer'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':100,'completion_tokens':1,'total_tokens':101}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            chosen.root_async_client=chosen.root_async_client.with_options(http_client=client)
            chosen.async_client=chosen.root_async_client.chat.completions
            middleware=ModelInputMiddleware()
            request=ModelRequest(model=chosen,messages=[HumanMessage(content='hello')],tools=[],runtime=SimpleNamespace(context=None))
            async def execute(req):return ModelResponse(result=[await req.model.ainvoke(req.messages)])
            first=await middleware.awrap_model_call(request,execute)
            assert calibration.KEY in first.result[0].response_metadata
            await middleware.awrap_model_call(request.override(messages=[*request.messages,*first.result]),execute)
        assert len(sent)==2 and calibration.KEY not in json.dumps(sent)
        await catalog.aclose()
    asyncio.run(run())


def test_graph_prepare_receives_response_format(monkeypatch):
    from pydantic import BaseModel
    from naiyue_test_agent.agent_runtime import build_runtime_agent
    from naiyue_test_agent.application import prepare_tool_registry
    from naiyue_test_agent.agent_runtime import request_preparation
    class Result(BaseModel):
        value: str
    chosen=identified(model(window=4000,reserve=20))
    seen=[];original=request_preparation.prepare_request
    # Selection imports the function directly; retain real preparation and stop before provider IO.
    from naiyue_test_agent.agent_runtime import call_observation
    async def prepare(state,runtime,prepared,**kwargs):
        seen.append(prepared.response_format)
        await original(state,runtime,prepared,**kwargs)
        raise ValueError('stop_before_network')
    monkeypatch.setattr(call_observation,'prepare_request',prepare)
    from runtime_context_helpers import RunIdentity
    # Use the normal typed context with an explicit session for this invocation.
    context=typed_context(identity=RunIdentity(session_id='s'))
    async def invoke():
        graph=build_runtime_agent(chosen,tool_registry=prepare_tool_registry([]),
            resolve_model=lambda sid,path:('A',chosen),resolve_active_tool_names=lambda *a:(),
            system_prompt='BASE',response_format=Result)
        with pytest.raises(ValueError,match='stop_before_network'):
            await graph.ainvoke({'messages':[HumanMessage(content='hello')]},context=context)
    asyncio.run(invoke())
    assert seen==[Result]
