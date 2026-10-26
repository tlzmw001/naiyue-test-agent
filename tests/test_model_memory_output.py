"""Explicit memory output capabilities use the pinned adapters without probing."""

import pytest

from naiyue_test_agent.model_access import ModelConfigError, load_model_catalog
from test_model_access import config_for_all_kinds, write_config


@pytest.mark.parametrize('name', ['official', 'gateway', 'claude', 'local'])
@pytest.mark.parametrize('mode', ['text', 'json_schema', 'function_calling'])
def test_memory_output_is_explicit_per_model_and_does_not_change_other_calls(tmp_path, name, mode):
    data = config_for_all_kinds()
    data['models'][name]['profile'] = {'memory_output': mode}
    catalog = load_model_catalog(write_config(tmp_path, data))
    assert catalog.get(name).profile['memory_output'] == mode
    for other in data['models']:
        if other != name:
            assert 'memory_output' not in (catalog.get(other).profile or {})


@pytest.mark.parametrize('mode', [True, None, 'auto', 'json_mode', 1])
def test_invalid_memory_output_does_not_silently_select_another_protocol(tmp_path, mode):
    data = config_for_all_kinds()
    data['models']['gateway']['profile'] = {'memory_output': mode}
    with pytest.raises(ModelConfigError, match='memory_output'):
        load_model_catalog(write_config(tmp_path, data))


@pytest.mark.parametrize('name', ['official', 'gateway', 'claude', 'local'])
@pytest.mark.parametrize('mode', ['json_schema', 'function_calling'])
def test_official_wrapper_binds_schema_and_output_limit_without_mutating_model(tmp_path, name, mode):
    from naiyue_test_agent.application.memory.memory_batches import response_schema
    from naiyue_test_agent.model_access.memory_output import prepare_memory_output
    data = config_for_all_kinds()
    data['models'][name]['profile'] = {'memory_output': mode}
    model = load_model_catalog(write_config(tmp_path, data)).get(name)
    original = model.model_dump()
    call, actual, overhead = prepare_memory_output(model, response_schema(), 77)
    assert actual == mode and overhead > 0
    raw = call.first.steps__['raw']
    if name == 'local':
        assert raw.bound.num_predict == 77 and 'num_predict' not in raw.kwargs
    else:assert raw.kwargs['max_completion_tokens' if name in ('official', 'gateway') else 'max_tokens'] == 77
    assert raw.kwargs['ls_structured_output_format']['kwargs']['method'] == mode
    assert model.model_dump() == original
    if name in ('official', 'gateway'):
        if mode == 'json_schema':
            assert raw.kwargs['response_format']['json_schema']['strict'] is True
        else:
            assert raw.kwargs['tools'][0]['function']['strict'] is True
            assert raw.kwargs['parallel_tool_calls'] is False


def test_text_default_uses_existing_cap_and_has_no_schema_overhead():
    from naiyue_test_agent.model_access.memory_output import prepare_memory_output
    from test_async_application import StreamingToolModel
    model = StreamingToolModel(replies=[])
    bound, mode, overhead = prepare_memory_output(model, {}, 99)
    assert mode == 'text' and overhead == 0 and bound.kwargs == {'max_tokens': 99}
    assert not model.seen


def test_sdk_protocol_fallback_is_rejected_before_any_request():
    from langchain_openai import ChatOpenAI
    from naiyue_test_agent.application.memory.memory_batches import response_schema
    from naiyue_test_agent.model_access.memory_output import prepare_memory_output
    model = ChatOpenAI(model='gpt-4', api_key='offline-placeholder')
    model.profile = {'memory_output': 'json_schema'}
    with pytest.raises(ValueError, match='memory_output_protocol_unavailable'):
        prepare_memory_output(model, response_schema(), 99)


def test_ollama_text_limit_reaches_native_options_without_losing_configured_sampling():
    import asyncio
    import json
    from http_observation_helpers import sdk_catalog, sdk_response
    from naiyue_test_agent.model_access import bind_model_output_limit
    async def scenario():
        sent = []
        def serve(request):
            sent.append(json.loads(request.content))
            return sdk_response(request, 'ollama')
        async with sdk_catalog('ollama', serve=serve) as catalog:
            model = catalog.get('A')
            model.temperature, model.num_ctx, model.num_predict = 0.2, 8192, 128
            assert (await bind_model_output_limit(model, 77).ainvoke('offline')).content == 'done'
            assert sent[0]['options'] == {'temperature': 0.2, 'num_ctx': 8192, 'num_predict': 77}
            assert model.num_predict == 128 and len(sent) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['openai', 'responses'])
@pytest.mark.parametrize('mode', ['text', 'json_schema', 'function_calling'])
def test_single_request_cap_overrides_configured_openai_default_without_mutation(kind, mode):
    import asyncio
    import json
    from http_observation_helpers import sdk_catalog
    from test_memory_batches import result
    from test_memory_structured_output import wire_response
    from naiyue_test_agent.application.memory.memory_batches import response_schema
    from naiyue_test_agent.model_access.memory_output import prepare_memory_output

    async def scenario():
        sent = []
        def serve(request):
            sent.append(json.loads(request.content))
            return wire_response(request, kind, mode, result())
        async with sdk_catalog(kind, serve=serve) as catalog:
            model = catalog.get('A')
            model.max_tokens = 16384
            model.profile = {'memory_output': mode}
            before = model.model_dump()
            bounded, _, _ = prepare_memory_output(model, response_schema(), 77)
            await bounded.ainvoke('offline')
            assert len(sent) == 1
            assert sent[0]['max_output_tokens' if kind == 'responses' else 'max_completion_tokens'] == 77
            assert model.model_dump() == before and model.max_tokens == 16384
    asyncio.run(scenario())
