"""Report privacy and reproducible input identities."""
import json
from types import SimpleNamespace
from scripts.probe_memory_semantics import safe_report


def test_report_filters_nested_nonvisible_message_blocks():
    value={'answer':[{'type':'reasoning','encrypted_content':'PRIVATE'},
        {'type':'thinking','thinking':'PRIVATE'}, {'type':'text','text':'visible','signature':'PRIVATE'}],
        'model_request':[{'data':{'content':[{'type':'redacted_thinking','data':'PRIVATE'},
            {'type':'text','text':'question'}]}}]}
    result=safe_report(value)
    assert 'PRIVATE' not in json.dumps(result)
    assert result['answer']==[{'type':'text','text':'visible'}]
    assert result['model_request'][0]['data']['content']==[{'type':'text','text':'question'}]


def test_effective_identity_changes_with_sampling_and_omits_secrets():
    from scripts.probe_memory_semantics import evaluation_model_identity
    from naiyue_test_agent.memory_settings import MemorySettings
    model=SimpleNamespace(metadata={},model_name='synthetic',temperature=0.2,top_p=None,
        max_retries=2,profile={'memory_output':'text'},model_kwargs={'token':'PRIVATE'})
    first=evaluation_model_identity(model,MemorySettings())
    model.temperature=0.7
    second=evaluation_model_identity(model,MemorySettings())
    assert first['settings_sha256']!=second['settings_sha256']
    assert 'PRIVATE' not in json.dumps(first)
    assert first['identity_status']=='partial'


def test_manifest_covers_actual_drivers():
    from scripts.evaluate_memory_optimization import evidence_manifest
    files=evidence_manifest()['source_sha256']
    for path in ('scripts/memory_space_time_probe.py','scripts/memory_space_time_labels.py',
        'naiyue_test_agent/tools/memory_writes.py','naiyue_test_agent/model_access/calibration_identity.py'):
        assert path in files


def test_protocol_identity_uses_selected_protocol_and_rubric(tmp_path):
    import hashlib
    from scripts.probe_memory_semantics import evaluation_protocol_identity
    protocol=tmp_path/'custom.json';protocol.write_text('{"version":2}')
    rubric=tmp_path/'rubric.md';rubric.write_text('frozen rubric')
    result=evaluation_protocol_identity(SimpleNamespace(suite='space-time',protocol=protocol))
    assert result==dict(protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),
        rubric_sha256=hashlib.sha256(rubric.read_bytes()).hexdigest())


def test_followup_callback_keeps_visible_answer_only():
    import asyncio
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import LLMResult,ChatGeneration
    from scripts.probe_memory_semantics import probe_callback
    from scripts.provider_acceptance_budget import RequestBudget
    from naiyue_test_agent.memory_settings import MemorySettings
    cb=probe_callback(RequestBudget(3),MemorySettings());cb.stages['answer']='followup'
    message=AIMessage(content=[dict(type='reasoning',encrypted_content='PRIVATE'),dict(type='text',text='answer')])
    asyncio.run(cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]),run_id='answer'))
    assert cb.outputs==[dict(stage='followup',answer=[dict(type='text',text='answer')])]
