"""Cache capability and policy are validated once on real catalog models."""

from copy import deepcopy

import pytest
from langchain_core.messages import HumanMessage

from naiyue_test_agent.model_access import ModelConfigError, load_model_catalog
from test_model_access import config_for_all_kinds, write_config


@pytest.mark.parametrize('model_id,protocol,retentions,expected', [
    ('official', 'openai', ['24h', 'in_memory'], {'prompt_cache_retention': 'in_memory'}),
    ('gateway', 'openai', ['in_memory', '24h'], {'prompt_cache_retention': 'in_memory'}),
    ('gateway', 'openai', ['24h'], {'prompt_cache_retention': '24h'}),
    ('official', 'openai_options', ['30m'], {'prompt_cache_options': {'mode': 'implicit', 'ttl': '30m'}}),
    ('gateway', 'openai_options', ['30m'], {'prompt_cache_options': {'mode': 'implicit', 'ttl': '30m'}}),
    ('claude', 'anthropic', ['1h', '5m'], {'cache_control': {'type': 'ephemeral', 'ttl': '5m'}}),
    ('claude', 'anthropic', ['1h'], {'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}),
])
@pytest.mark.parametrize('api', ['chat_completions', 'responses'])
@pytest.mark.parametrize('request_policy', [None, {'timeout_seconds': 70}, {'prompt_cache': {}}])
def test_default_shortest_reaches_adapter_payload(tmp_path, model_id, protocol, retentions, expected, api, request_policy):
    config = config_for_all_kinds()
    entry = config['models'][model_id]
    entry['profile'] = {'image_inputs': False, 'prompt_cache': {'protocol': protocol, 'retentions': retentions}}
    if model_id != 'claude':
        entry['api'] = api
    if request_policy is not None:
        entry['request'] = request_policy
    before = deepcopy(config)
    model = load_model_catalog(write_config(tmp_path, config)).get(model_id)
    profile = deepcopy(model.profile)
    payload = model._get_request_payload([HumanMessage(content='offline input')])
    assert {key: payload[key] for key in expected} == expected
    assert not {'prompt_cache_key'} & payload.keys()
    assert model.profile == profile and config == before
    for key in {'prompt_cache_retention', 'prompt_cache_options', 'cache_control'} - expected.keys():
        assert key not in payload


@pytest.mark.parametrize('model_id,protocol,retentions,selected,expected', [
    ('official', 'openai', ['in_memory', '24h'], '24h', {'prompt_cache_retention': '24h'}),
    ('gateway', 'openai_options', ['30m'], '30m', {'prompt_cache_options': {'mode': 'implicit', 'ttl': '30m'}}),
    ('claude', 'anthropic', ['5m', '1h'], '1h', {'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}),
])
def test_explicit_retention_overrides_only_model_default(tmp_path, model_id, protocol, retentions, selected, expected):
    config = config_for_all_kinds()
    config['models'][model_id]['profile'] = {'prompt_cache': {'protocol': protocol, 'retentions': retentions}}
    config['models'][model_id]['request'] = {'prompt_cache': {'retention': selected}, 'sdk_max_retries': 0}
    catalog = load_model_catalog(write_config(tmp_path, config))
    model = catalog.get(model_id)
    assert {key: model._get_request_payload([HumanMessage(content='input')])[key] for key in expected} == expected
    assert model.max_retries == 0
    for other in catalog.model_ids:
        if other != model_id and other != 'local':
            assert not {'prompt_cache_retention', 'prompt_cache_options', 'cache_control'} & catalog.get(other)._get_request_payload(
                [HumanMessage(content='other')]).keys()


@pytest.mark.parametrize('declared', [False, 'missing'])
def test_unknown_or_unsupported_does_not_guess_from_model_names(tmp_path, declared):
    config = config_for_all_kinds()
    config['models']['official']['model'] = 'gpt-5.5'
    config['models']['gateway']['model'] = 'claude-sonnet-4-6'
    config['models']['claude']['model'] = 'claude-sonnet-4-6'
    if declared is False:
        for entry in config['models'].values():
            entry['profile'] = {'prompt_cache': False}
    catalog = load_model_catalog(write_config(tmp_path, config))
    for model_id in ('official', 'gateway', 'claude'):
        payload = catalog.get(model_id)._get_request_payload([HumanMessage(content='input')])
        assert not {'prompt_cache_retention', 'prompt_cache_options', 'cache_control', 'prompt_cache_key'} & payload.keys()
    local_payload = catalog.get('local')._chat_params([HumanMessage(content='local input')])
    assert not {'prompt_cache_retention', 'prompt_cache_options', 'cache_control', 'prompt_cache_key'} & local_payload.keys()


@pytest.mark.parametrize('capability', [None, True, [], 'PRIVATE', {},
    {'protocol': 'PRIVATE', 'retentions': ['in_memory']},
    {'protocol': ['PRIVATE'], 'retentions': ['in_memory']},
    {'protocol': 'openai', 'retentions': []},
    {'protocol': 'openai', 'retentions': 'in_memory'},
    {'protocol': 'openai', 'retentions': ['in_memory', 'in_memory']},
    {'protocol': 'openai', 'retentions': [['PRIVATE']]},
    {'protocol': 'openai', 'retentions': [True]},
    {'protocol': 'openai', 'retentions': ['1h']},
    {'protocol': 'openai_options', 'retentions': ['24h']},
    {'protocol': 'openai', 'retentions': ['in_memory'], 'extra': 'PRIVATE'},
])
def test_invalid_capability_fails_without_echoing_private_values(tmp_path, capability):
    config = config_for_all_kinds()
    config['models']['gateway']['profile'] = {'prompt_cache': capability}
    with pytest.raises(ModelConfigError) as caught:
        load_model_catalog(write_config(tmp_path, config))
    assert 'prompt_cache' in str(caught.value)
    assert 'PRIVATE' not in str(caught.value) and 'gateway-test-key' not in str(caught.value)


@pytest.mark.parametrize('override', [None, False, 'PRIVATE', [], {'extra': 'PRIVATE'},
    {'retention': '1h'}, {'retention': True}, {'retention': ['PRIVATE']}, {'retention': None}])
def test_invalid_or_unsupported_override_is_a_configuration_error(tmp_path, override):
    config = config_for_all_kinds()
    entry = config['models']['gateway']
    entry['profile'] = {'prompt_cache': {'protocol': 'openai', 'retentions': ['in_memory', '24h']}}
    entry['request'] = {'prompt_cache': override}
    with pytest.raises(ModelConfigError) as caught:
        load_model_catalog(write_config(tmp_path, config))
    assert 'prompt_cache' in str(caught.value) and 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('declared', [False, 'missing'])
def test_explicit_policy_requires_a_supporting_capability(tmp_path, declared):
    config = config_for_all_kinds()
    entry = config['models']['gateway']
    if declared is False:
        entry['profile'] = {'prompt_cache': False}
    entry['request'] = {'prompt_cache': {'retention': '24h'}}
    with pytest.raises(ModelConfigError, match='prompt_cache'):
        load_model_catalog(write_config(tmp_path, config))


@pytest.mark.parametrize('model_id,protocol,retentions', [
    ('local', 'openai', ['in_memory']), ('claude', 'openai', ['24h']),
    ('official', 'anthropic', ['5m']), ('gateway', 'anthropic', ['5m']),
])
def test_cache_protocol_must_match_transport(tmp_path, model_id, protocol, retentions):
    config = config_for_all_kinds()
    config['models'][model_id]['profile'] = {'prompt_cache': {'protocol': protocol, 'retentions': retentions}}
    with pytest.raises(ModelConfigError, match='prompt_cache'):
        load_model_catalog(write_config(tmp_path, config))
