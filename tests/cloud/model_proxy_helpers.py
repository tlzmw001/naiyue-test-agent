"""Temporary explicit model configuration; no developer credentials or endpoints."""
import json

from cloud_helpers import settings


def model_settings(tmp_path,*,upstream='http://127.0.0.1:29991',**changes):
    data=dict(schema_version=1,default_model_id='chat',providers={
        'o':dict(kind='openai',api_key='fixture-upstream-openai'),
        'a':dict(kind='anthropic',api_key='fixture-upstream-anthropic'),
        'l':dict(kind='ollama',base_url=upstream)},models={
        'chat':dict(provider_id='o',model='fixture-chat',api='chat_completions',request={'sdk_max_retries':0}),
        'responses':dict(provider_id='o',model='fixture-responses',api='responses',request={'sdk_max_retries':0}),
        'anthropic':dict(provider_id='a',model='fixture-anthropic',request={'sdk_max_retries':0}),
        'ollama':dict(provider_id='l',model='fixture-ollama')})
    path=tmp_path/'model-fixture.json';path.write_text(json.dumps(data));path.chmod(0o600)
    proxy=dict(config_path=path,upstreams={'o':upstream+'/v1','a':upstream},**changes)
    return settings(tmp_path,model_proxy=proxy),data
