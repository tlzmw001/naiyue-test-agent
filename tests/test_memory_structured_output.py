"""Native SDK requests remain one call, with strict local independent commits."""

import asyncio
import json

import httpx
import httpx2
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from http_observation_helpers import sdk_catalog, sdk_response
from naiyue_test_agent.application.memory.memory_batches import parse_model_result
from naiyue_test_agent.application.memory.memory_queries import MemoryReadError
from test_memory_batches import archive, save, result
from test_memory_flat_contract import flat
from test_memory_job_runner import fixture


def tool_result(value, *, name='MemoryResult', **changes):
    args = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return AIMessage(content='', additional_kwargs={'tool_calls': [dict(id='return-1', type='function',
        function=dict(name=name, arguments=args))]}, response_metadata={'finish_reason': 'tool_calls'}, **changes)


def test_function_result_is_a_return_value_and_preserves_per_item_validation():
    value = result(proposals=[flat(), {'invalid': True}])
    assert parse_model_result(tool_result(value), type('Settings', (), dict(overview_chars=1200, max_proposals=8))(),
        output_mode='function_calling') == value


@pytest.mark.parametrize('mutation', ['wrong_name', 'duplicate', 'multiple', 'truncated', 'refusal', 'invalid_call'])
def test_function_protocol_does_not_repair_or_execute_untrusted_calls(mutation):
    from test_memory_batches import settings
    response = tool_result(result())
    if mutation == 'wrong_name':response = tool_result(result(), name='bash')
    elif mutation == 'duplicate':response = tool_result(json.dumps(result()).replace('"proposals": []', '"proposals": [], "proposals": []'))
    elif mutation == 'multiple':response.tool_calls.append(dict(name='bash', args={}, id='other', type='tool_call'))
    elif mutation == 'truncated':response.response_metadata['finish_reason'] = 'length'
    elif mutation == 'refusal':response.additional_kwargs['refusal'] = 'declined'
    else:response.invalid_tool_calls.append(dict(name='MemoryResult', args='{', id='bad', error='invalid', type='invalid_tool_call'))
    with pytest.raises(MemoryReadError):
        parse_model_result(response, settings(), output_mode='function_calling')


def wire_response(request, kind, mode, value):
    response = sdk_response(request, kind)
    body = response.json()
    text = json.dumps(value, ensure_ascii=False)
    if kind == 'openai':
        message = dict(role='assistant', content=text)
        if mode == 'function_calling':
            message.update(content=None, tool_calls=[dict(id='return-1', type='function',
                function=dict(name='MemoryResult', arguments=text))])
        body['choices'][0].update(message=message, finish_reason='tool_calls' if mode == 'function_calling' else 'stop')
    elif kind == 'responses':
        body['output'] = ([dict(type='function_call', id='fc_return', call_id='return-1', name='MemoryResult',
            arguments=text, status='completed')] if mode == 'function_calling' else
            [dict(type='message', id='msg_return', role='assistant', status='completed',
                content=[dict(type='output_text', text=text, annotations=[])])])
    elif kind == 'anthropic':
        body['content'] = ([dict(type='tool_use', id='return-1', name='MemoryResult', input=value)]
            if mode == 'function_calling' else [dict(type='text', text=text)])
        body['stop_reason'] = 'tool_use' if mode == 'function_calling' else 'end_turn'
    else:
        body['message']['content'] = text if mode == 'json_schema' else ''
        if mode == 'function_calling':
            body['message']['tool_calls'] = [dict(function=dict(name='MemoryResult', arguments=value))]
    return (httpx2 if kind == 'anthropic' else httpx).Response(200, json=body)


@pytest.mark.parametrize('kind', ['openai', 'responses', 'anthropic', 'ollama'])
@pytest.mark.parametrize('mode', ['json_schema', 'function_calling'])
def test_real_adapter_worker_keeps_partial_success_usage_and_single_request(archive, tmp_path, kind, mode):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='使用.venv', additional_kwargs={'naiyue_human_input': True})])
        sent = []
        def serve(request):
            sent.append(json.loads(request.content))
            return wire_response(request, kind, mode, result(proposals=[flat(), {'invalid': True}]))
        async with sdk_catalog(kind, serve=serve) as catalog:
            model = catalog.get('A')
            model.profile = {**(model.profile or {}), 'memory_output': mode}
            service, job, events = fixture(archive, tmp_path, model)
            try:
                await drive(service, job)
                assert job.result_status == 'partial' and job.failed == job.saved == job.overview_saved == 1
                assert len(sent) == 1
                starts = [e for e in events if e.event_type == 'model_call_started']
                ends = [e for e in events if e.event_type == 'model_call_finished']
                assert len(starts) == len(ends) == 1 and starts[0].data.invocation_id == ends[0].data.invocation_id
                assert starts[0].data.purpose == 'memory'
                body = sent[0]
                if mode == 'function_calling':
                    assert len(body['tools']) == 1
                elif kind in ('openai', 'responses'):
                    assert body.get('response_format', body.get('text', {}).get('format', {}))['type'] == 'json_schema'
                elif kind == 'anthropic':assert body['output_config']['format']['type'] == 'json_schema'
                else:assert body['format']['type'] == 'object'
                if kind == 'ollama':assert body['options']['num_predict'] == service.settings.max_output_tokens
                else:assert body.get('max_completion_tokens', body.get('max_output_tokens', body.get('max_tokens'))) == service.settings.max_output_tokens
            finally:
                service.memory.close()
    asyncio.run(scenario())


def test_schema_budget_fails_before_request_and_does_not_switch_protocol(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='使用.venv')])
        calls = []
        async with sdk_catalog(serve=lambda request: calls.append(request)) as catalog:
            model = catalog.get('A'); model.profile = {'memory_output': 'json_schema'}
            service, job, _ = fixture(archive, tmp_path, model, max_input_tokens=1100)
            try:
                await drive(service, job)
                assert job.result_status == 'failed' and job.overview_saved == job.saved == 0
                assert not calls
            finally:service.memory.close()
    asyncio.run(scenario())
