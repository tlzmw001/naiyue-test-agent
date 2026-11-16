"""Calibration identity uses actual SDK route and effective thinking."""
import pytest
from naiyue_test_agent.model_access import catalog_from_configuration
from naiyue_test_agent.model_access.configuration import parse_model_configuration


def config(kind='openai_compatible',key='fixture-key',route='https://example.test/v1'):
    return parse_model_configuration({'schema_version':1,'default_model_id':'m',
        'providers':{'p':{'kind':kind,'api_key':key,'base_url':route}},
        'models':{'m':{'provider_id':'p','model':'fixture','api':'chat_completions',
            'thinking':{'supported_levels':['low','high'],'default_level':'low'}}}})


def test_catalog_identity_credential_rotation_route_and_thinking():
    first=catalog_from_configuration(config());second=catalog_from_configuration(config(key='rotated'))
    metadata=first.get('m').metadata
    assert 'naiyue_context_estimation' in metadata
    original=metadata['naiyue_context_estimation']
    assert original==second.get('m').metadata['naiyue_context_estimation']
    third=catalog_from_configuration(config(route='https://other.test/v1'))
    assert original!=third.get('m').metadata['naiyue_context_estimation']
    high=first.with_thinking('m','high')
    assert high.metadata is not metadata
    assert high.metadata['naiyue_context_estimation']!=original
    assert first.get('m').metadata['naiyue_context_estimation']==original
    assert 'fixture-key' not in str(original) and 'example.test' not in str(original)


@pytest.mark.parametrize('kind',['openai','anthropic','ollama'])
def test_effective_sdk_endpoint_and_adapters(kind,monkeypatch):
    from naiyue_test_agent.agent_runtime.context_calibration import measure_request
    provider={'kind':kind}
    if kind!='ollama':provider['api_key']='synthetic'
    if kind=='ollama':provider['base_url']='http://127.0.0.1:11434'
    entry={'provider_id':'p','model':'fixture'}
    if kind=='openai':
        entry['api']='chat_completions'
        monkeypatch.setenv('OPENAI_BASE_URL','https://environment.test/v1')
    parsed=parse_model_configuration({'schema_version':1,'default_model_id':'m','providers':{'p':provider},'models':{'m':entry}})
    catalog=catalog_from_configuration(parsed)
    metadata=catalog.get('m').metadata['naiyue_context_estimation']
    assert metadata and metadata['kind']==kind
    assert measure_request(catalog.get('m'),None,[],[]).identity is not None
    assert 'synthetic' not in str(metadata)


def test_runner_profile_survives_proxy_route_and_token_renewal():
    import asyncio
    from pydantic import SecretStr
    from naiyue_test_agent.cloud_runner.models import RunnerModels
    from naiyue_test_agent.contracts.cloud_models import RunnerModelProfile
    from naiyue_test_agent.contracts.cloud_runner import RunnerBootstrap
    from uuid import uuid4
    public=config().public_configuration
    b=RunnerBootstrap(task_id=uuid4().hex, project_id=str(uuid4()), input_id='a'*64,
        instance_id=str(uuid4()), generation=1, boot_id=str(uuid4()), state_epoch=str(uuid4()),
        release_id='test-release', endpoint='http://127.0.0.1:28102', token='task-test-only',
        expires_at=9999999999.0, models=RunnerModelProfile(profile_id='a'*64,configuration=public))
    async def scenario():
        first=RunnerModels(b)
        other=RunnerModels(b.model_copy(update={'endpoint':'http://127.0.0.1:29991'}))
        changed=RunnerModels(b.model_copy(update={'models':b.models.model_copy(update={'profile_id':'b'*64})}))
        try:
            original=first.catalog.get('m').metadata['naiyue_context_estimation']
            assert original==other.catalog.get('m').metadata['naiyue_context_estimation']
            assert original!=changed.catalog.get('m').metadata['naiyue_context_estimation']
            first.renew(SecretStr('rotated'),99999999999.0)
            assert original==first.catalog.get('m').metadata['naiyue_context_estimation']
            assert b.token.get_secret_value() not in str(original)
        finally:
            await first.aclose()
            await other.aclose()
            await changed.aclose()
    asyncio.run(scenario())
