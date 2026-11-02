"""Pure model parsing, secret-free task profiles and exact fixed upstream policy."""
import json
from hashlib import sha256

import pytest
from pydantic import ValidationError

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.model_access import load_model_catalog
from model_proxy_helpers import model_settings
from test_runner_control import prepared


def test_cloud_model_configuration_is_pure_and_bootstrap_contains_no_root_secrets(tmp_path,monkeypatch):
    config,data=model_settings(tmp_path)
    import naiyue_test_agent.model_access as access
    def forbidden(*args,**kwargs):raise AssertionError('Coordinator must not construct model SDKs')
    monkeypatch.setattr(access,'_build_model',forbidden)
    with open_cloud_runtime(config) as runtime:
        assert runtime.models.configured
        b=prepared(runtime)
        raw=b.model_dump_json()
        for provider in data['providers'].values():
            if 'api_key' in provider:assert provider['api_key'] not in raw
            if 'base_url' in provider:assert provider['base_url'] not in raw
        assert 'config_path' not in raw and 'upstreams' not in raw
        assert b.models.profile_id==runtime.models.profile.profile_id
        assert runtime.registry.get_task(b.task_id).model_profile_id==b.models.profile_id
        token=runtime.credentials.repository.find(sha256(b.token.get_secret_value().encode()).hexdigest())
        assert 'models:invoke' in token.scopes
        assert b.models.configuration['default_model_id']=='chat'
        assert b.models.configuration['providers']=={k:{'kind':v['kind']} for k,v in data['providers'].items()}


def test_model_key_rotation_preserves_policy_identity_but_endpoint_change_does_not(tmp_path):
    config,data=model_settings(tmp_path)
    with open_cloud_runtime(config) as runtime:first=runtime.models.profile.profile_id
    data['providers']['o']['api_key']='fixture-rotated-key'
    config.model_proxy.config_path.write_text(json.dumps(data))
    with open_cloud_runtime(config) as runtime:assert runtime.models.profile.profile_id==first
    changed=config.model_copy(update={'model_proxy':config.model_proxy.model_copy(update={'upstreams':{'o':'http://127.0.0.1:29992/v1','a':'http://127.0.0.1:29991'}})})
    with open_cloud_runtime(changed) as runtime:assert runtime.models.profile.profile_id!=first


@pytest.mark.parametrize('upstreams',[{}, {'o':'http://127.0.0.1:29991/v1','a':'http://127.0.0.1:29991','wrong':'https://example.invalid'}])
def test_missing_or_extra_upstream_configuration_fails_before_task_admission(tmp_path,upstreams):
    config,_=model_settings(tmp_path)
    config=config.model_copy(update={'model_proxy':config.model_proxy.model_copy(update={'upstreams':upstreams})})
    with pytest.raises(ValueError,match='model_upstream_configuration'):
        with open_cloud_runtime(config):pass


def test_model_configuration_reuses_existing_native_parser_and_preserves_model_settings(tmp_path):
    config,data=model_settings(tmp_path)
    data['models']['chat'].update(thinking={'supported_levels':['low','high'],'default_level':'high'},
        context_window_tokens=32768,max_output_tokens=4096)
    config.model_proxy.config_path.write_text(json.dumps(data))
    from naiyue_test_agent.model_access.configuration import read_model_configuration
    parsed=read_model_configuration(config.model_proxy.config_path)
    catalog=load_model_catalog(config.model_proxy.config_path)
    assert parsed.default_model_id==catalog.default_model_id=='chat'
    assert parsed.models['chat']['selection'].selected=='high'
    assert catalog.get('chat').metadata['naiyue_model_limits']['context_window_tokens']==32768
    assert parsed.models['chat']['request_settings']['max_retries']==0


@pytest.mark.parametrize('url',['http://public.example.invalid/v1','https://example.invalid/v1?secret=bad','https://user:pass@example.invalid/v1'])
def test_upstream_urls_are_explicit_service_roots_without_auth_or_queries(tmp_path,url):
    with pytest.raises(ValidationError):model_settings(tmp_path,upstream=url)


@pytest.mark.parametrize('boundary',['count','identity'])
def test_model_profile_validates_control_identity_budget_before_task_admission(tmp_path,boundary):
    config,data=model_settings(tmp_path)
    names=[f'm{i}' for i in range(129)] if boundary=='count' else ['m'*129]
    data['models']={name:dict(provider_id='o',model='fixture-chat',api='chat_completions') for name in names}
    # Keep an Anthropic model so the existing deployment override is still used.
    if boundary=='identity':data['models']['anthropic']=dict(provider_id='a',model='fixture-anthropic')
    else:data['models'][names[-1]]=dict(provider_id='a',model='fixture-anthropic')
    data['default_model_id']=names[0]
    config.model_proxy.config_path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        with open_cloud_runtime(config):pass


def test_expired_bootstrap_rotates_credentials_without_changing_same_generation_profile(tmp_path):
    from naiyue_test_agent.cloud_runner.state import read_json,write_json
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r);path=r.control.bootstrap.directory(r.registry.get_task(b.task_id))/'bootstrap.json'
        original=read_json(path);write_json(path,original|{'expires_at':1})
    changed=config.model_copy(update={'model_proxy':config.model_proxy.model_copy(update={
        'upstreams':{'o':'http://127.0.0.1:29992/v1','a':'http://127.0.0.1:29991'}})})
    with open_cloud_runtime(changed) as r:
        renewed=r.control.prepare(b.task_id)
        assert renewed.generation==b.generation and renewed.boot_id==b.boot_id
        assert renewed.models==b.models and renewed.token!=b.token
        assert r.registry.get_task(b.task_id).model_profile_id==b.models.profile_id
        assert renewed.models.profile_id!=r.models.profile.profile_id
