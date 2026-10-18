"""One finite usage view over native adapters; cache details are input subsets."""

from copy import deepcopy

import pytest


def normalized(usage):
    from naiyue_test_agent.model_access.usage import normalize_model_usage
    return normalize_model_usage(usage)


@pytest.mark.parametrize('usage, expected', [
    (None, (None, None, None, None)),
    ({}, (None, None, None, None)),
    ({'input_tokens': 0, 'output_tokens': 0, 'input_token_details': {'cache_read': 0, 'cache_creation': 0}}, (0, 0, 0, 0)),
    ({'input_tokens': 8200, 'output_tokens': 30, 'input_token_details': {'cache_read': 7000}}, (8200, 30, 7000, None)),
    ({'input_tokens': 8200, 'output_tokens': 30, 'input_token_details': {
        'cache_read': 7000, 'cache_creation': 0, 'ephemeral_5m_input_tokens': 600,
        'ephemeral_1h_input_tokens': 400}}, (8200, 30, 7000, 1000)),
    ({'input_tokens': 8200, 'input_token_details': {'cache_creation': 1000,
        'ephemeral_5m_input_tokens': 600, 'ephemeral_1h_input_tokens': 400}}, (8200, None, None, 1000)),
    ({'input_token_details': {'cache_creation': 12, 'ephemeral_5m_input_tokens': 0,
        'ephemeral_1h_input_tokens': 0}}, (None, None, None, 12)),
    ({'input_token_details': {'cache_creation': 0, 'ephemeral_5m_input_tokens': 0}}, (None, None, None, 0)),
    ({'input_token_details': {'cache_read': 3, 'priority_cache_read': 7,
        'flex_cache_creation': 5}}, (None, None, 10, 5)),
])
def test_usage_shapes_are_finite_additive_and_do_not_mutate(usage, expected):
    before = deepcopy(usage)
    result = normalized(usage)
    assert result == dict(zip(('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'), expected))
    assert usage == before


@pytest.mark.parametrize('field', ['input_tokens', 'output_tokens', 'cache_read', 'cache_creation',
                                    'priority_cache_read', 'ephemeral_5m_input_tokens'])
@pytest.mark.parametrize('bad', [True, -1, 1.5, 'PRIVATE_VALUE'])
def test_invalid_statistics_are_unknown_observable_and_content_free(field, bad, caplog):
    usage = {'input_tokens': 100, 'output_tokens': 8, 'input_token_details': {'cache_read': 20, 'cache_creation': 10}}
    if field in ('input_tokens', 'output_tokens'):
        usage[field] = bad
        target = field
    else:
        usage['input_token_details'][field] = bad
        target = 'cache_read_tokens' if field.endswith('cache_read') else 'cache_write_tokens'
    result = normalized(usage)
    assert result[target] is None
    assert result['output_tokens'] == (None if field == 'output_tokens' else 8)
    assert 'model_usage_invalid' in caplog.text
    assert 'PRIVATE_VALUE' not in caplog.text


@pytest.mark.parametrize('details', [{'cache_read': 101}, {'cache_creation': 101},
                                    {'cache_read': 60, 'cache_creation': 50}])
def test_cache_subsets_cannot_exceed_known_total_input(details, caplog):
    assert normalized({'input_tokens': 100, 'output_tokens': 5, 'input_token_details': details}) == {
        'input_tokens': 100, 'output_tokens': 5, 'cache_read_tokens': None, 'cache_write_tokens': None}
    assert 'model_usage_invalid' in caplog.text


@pytest.mark.parametrize('usage', [[], {'input_token_details': []}, {'input_token_details': 'PRIVATE_BODY'}])
def test_malformed_usage_container_is_not_an_execution_error(usage, caplog):
    assert normalized(usage) == dict.fromkeys(('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'))
    assert 'model_usage_invalid' in caplog.text and 'PRIVATE_BODY' not in caplog.text


@pytest.mark.parametrize('api', ['chat', 'responses', 'anthropic'])
@pytest.mark.parametrize('tier', [None, 'priority', 'flex'])
def test_native_adapters_keep_total_input_and_normalize_cache_details(api, tier):
    from langchain_openai.chat_models.base import _create_usage_metadata, _create_usage_metadata_responses
    from langchain_anthropic.chat_models import _create_usage_metadata as anthropic_usage
    from anthropic.types import Usage
    if api == 'anthropic':
        usage = anthropic_usage(Usage(input_tokens=200, output_tokens=30, cache_read_input_tokens=7000,
            cache_creation_input_tokens=1000, cache_creation={
                'ephemeral_5m_input_tokens': 600, 'ephemeral_1h_input_tokens': 400}))
    elif api == 'chat':
        usage = _create_usage_metadata({'prompt_tokens': 8200, 'completion_tokens': 30,
            'prompt_tokens_details': {'cached_tokens': 7000, 'cache_write_tokens': 1000}}, tier)
    else:
        usage = _create_usage_metadata_responses({'input_tokens': 8200, 'output_tokens': 30,
            'input_tokens_details': {'cached_tokens': 7000, 'cache_write_tokens': 1000}}, tier)
    assert normalized(usage) == {'input_tokens': 8200, 'output_tokens': 30,
                                 'cache_read_tokens': 7000, 'cache_write_tokens': 1000}
