"""Interface and vision capability are separate explicit model contracts."""

import pytest

from naiyue_test_agent.model_access import ModelConfigError, load_model_catalog
from test_model_access import config_for_all_kinds, write_config


def configured():
    data = config_for_all_kinds()
    for name in ('official', 'gateway'):
        data['models'][name]['api'] = 'chat_completions'
    return data


def test_missing_openai_api_is_a_startup_error(tmp_path):
    data = configured()
    for name in ('official', 'gateway'):
        data['models'][name].pop('api')
    with pytest.raises(ModelConfigError, match='api'):
        load_model_catalog(write_config(tmp_path, data))


@pytest.mark.parametrize(('api', 'responses'), [('chat_completions', False), ('responses', True)])
def test_api_and_profile_are_applied_to_original_model(tmp_path, api, responses):
    data = configured()
    data['models']['gateway'].update(api=api, profile={'image_inputs': True})
    model = load_model_catalog(write_config(tmp_path, data)).get('gateway')
    assert model.use_responses_api is responses
    assert model.profile['image_inputs'] is True


@pytest.mark.parametrize('api', [None, True, 1, [], 'auto', 'chat/completions'])
def test_invalid_api_is_rejected_without_fallback(tmp_path, api):
    data = configured(); data['models']['gateway']['api'] = api
    with pytest.raises(ModelConfigError, match='api'):
        load_model_catalog(write_config(tmp_path, data))


@pytest.mark.parametrize('profile', [None, {'image_inputs': 1}, {'image_inputs': 'true'}, {'unknown': True}])
def test_invalid_profile_is_a_startup_error(tmp_path, profile):
    data = configured(); data['models']['gateway']['profile'] = profile
    with pytest.raises(ModelConfigError, match='profile'):
        load_model_catalog(write_config(tmp_path, data))


def test_unknown_capability_is_conservative_and_explicit_false_overrides_known_profile(tmp_path):
    data = configured()
    data['models']['official'].update(model='gpt-4o', profile={'image_inputs': False})
    catalog = load_model_catalog(write_config(tmp_path, data))
    assert catalog.get('official').profile['image_inputs'] is False
    assert catalog.get('gateway').profile.get('image_inputs') is not True


@pytest.mark.parametrize('name', ['claude', 'local'])
def test_non_openai_adapters_do_not_accept_an_openai_api(tmp_path, name):
    data = configured(); data['models'][name]['api'] = 'responses'
    with pytest.raises(ModelConfigError, match='only applies'):
        load_model_catalog(write_config(tmp_path, data))
