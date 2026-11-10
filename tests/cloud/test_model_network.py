"""Model network policy reaches real native SDK clients and the cloud relay."""
import asyncio
import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from starlette.responses import Response

from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.web_settings import NetworkSettings
from http_observation_helpers import sdk_response
from model_http_helpers import live_app


@asynccontextmanager
async def endpoint(kind, seen):
    async def app(scope, receive, send):
        raw = bytearray()
        while True:
            message = await receive()
            raw.extend(message.get('body', b''))
            if not message.get('more_body'):
                break
        seen.append((scope['path'], dict(scope['headers'])))
        response = sdk_response(SimpleNamespace(content=bytes(raw)), kind)
        await Response(response.content, status_code=response.status_code,
                       headers=dict(response.headers))(scope, receive, send)
    async with live_app(app) as url:
        yield url


def native(kind, url):
    if kind == 'anthropic':
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model='synthetic', api_key='test-only', base_url=url, max_tokens=16, max_retries=0)
    if kind == 'ollama':
        from langchain_ollama import ChatOllama
        return ChatOllama(model='synthetic', base_url=url)
    from langchain_openai import ChatOpenAI
    from naiyue_test_agent.model_access.responses_stream import ResponsesChatOpenAI
    cls = ResponsesChatOpenAI if kind == 'responses' else ChatOpenAI
    return cls(model='synthetic', api_key='test-only', base_url=url + '/v1', max_retries=0,
               use_responses_api=kind == 'responses')


@pytest.mark.parametrize('kind', ['openai', 'responses', 'anthropic', 'ollama'])
@pytest.mark.parametrize('mode', ['auto', 'direct', 'proxy', 'bypass', 'auto_bypass'])
@pytest.mark.parametrize('sync', [False, True])
def test_native_route_and_owned_close(monkeypatch, kind, mode, sync):
    async def scenario():
        proxied, environment, direct = [], [], []
        async with (endpoint(kind, proxied) as proxy, endpoint(kind, environment) as env_proxy,
                    endpoint(kind, direct) as target):
            for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'OPENAI_PROXY', 'ANTHROPIC_PROXY'):
                monkeypatch.delenv(key, raising=False)
            monkeypatch.setenv('HTTP_PROXY', env_proxy)
            network = NetworkSettings(mode='proxy' if mode == 'bypass' else 'auto' if mode == 'auto_bypass' else mode,
                proxy=proxy, no_proxy=['127.0.0.1'] if mode in ('bypass', 'auto_bypass') else [])
            model = native(kind, target)
            original = (model._client._client, model._async_client._client) if kind == 'ollama' else ()
            catalog = ModelCatalog('A', {'A': model})
            try:
                catalog.prepare_async_clients(network=network)
                result = await asyncio.to_thread(model.invoke, 'test') if sync else await model.ainvoke('test')
                if kind == 'responses':
                    assert [(item['name'], item['args']) for item in result.tool_calls] == [
                        ('read', {'path': 'left'}), ('read', {'path': 'right'})]
                else:
                    assert result.content == 'done'
                assert (len(proxied), len(environment), len(direct)) == (
                    (0, 1, 0) if mode == 'auto' else (1, 0, 0) if mode == 'proxy' else (0, 0, 1))
            finally:
                await catalog.aclose()
            assert all(client.is_closed for client, _ in catalog._owned_http_clients)
            assert all(client.is_closed for client in original)
    asyncio.run(scenario())


def test_proxy_authentication_and_no_direct_fallback(monkeypatch):
    async def scenario():
        seen = []
        async with endpoint('openai', seen) as proxy:
            model = native('openai', 'http://model.invalid')
            catalog = ModelCatalog('A', {'A': model})
            try:
                catalog.prepare_async_clients(network=NetworkSettings(mode='proxy', proxy=proxy,
                    proxy_username='user@example', proxy_password='pass:/word'))
                assert (await model.ainvoke('test')).content == 'done'
                expected = b'Basic ' + base64.b64encode(b'user@example:pass:/word')
                assert seen[0][1][b'proxy-authorization'] == expected
            finally:
                await catalog.aclose()
        direct = []
        async with endpoint('openai', direct) as target:
            model = native('openai', target)
            catalog = ModelCatalog('A', {'A': model})
            try:
                catalog.prepare_async_clients(network=NetworkSettings(mode='proxy', proxy=proxy))
                from openai import APIConnectionError
                with pytest.raises(APIConnectionError):
                    await model.ainvoke('test')
                assert direct == []
            finally:
                await catalog.aclose()
    asyncio.run(scenario())


def test_local_settings_are_independent_and_invalid_policy_is_observable(tmp_path, caplog):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[network]\nmode="direct"\n[model_network]\nmode="proxy"\nproxy="http://proxy.invalid"\n'
                    'proxy_username="PRIVATE_USER"\nproxy_password="PRIVATE_PASSWORD"\nno_proxy=["10.*"]\n')
    settings = load_settings(path)
    assert settings.network.mode == 'direct'
    assert settings.model_network.mode == 'proxy' and settings.model_network.no_proxy == ('10.0.0.0/8',)
    assert settings.model_network.proxy_password == 'PRIVATE_PASSWORD'
    assert 'PRIVATE_PASSWORD' not in settings.model_dump_json() + repr(settings)
    path.write_text('[model_network]\nmode="proxy"\n')
    with pytest.raises(ValueError, match='Model network'):
        load_settings(path)
    assert 'model_network_settings_invalid' in caplog.text
    assert 'PRIVATE_PASSWORD' not in caplog.text


def test_service_deferred_models_receive_the_separate_policy(monkeypatch, tmp_path):
    from naiyue_test_agent import application
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.config_paths import StorageSettings
    from naiyue_test_agent.settings import AppSettings
    calls = []
    catalog = ModelCatalog('A', {})
    monkeypatch.setattr(catalog, 'prepare_async_clients', lambda **kwargs: calls.append(kwargs['network']))
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
    async def scenario():
        settings = AppSettings(storage=StorageSettings(data_dir=tmp_path / 'data'),
            model_network=NetworkSettings(mode='direct'))
        async with open_service_application('unused', tools=(), system_prompt='', response_format=None, settings=settings) as app:
            assert calls == []
            await app.models._catalog.get()
            assert calls == [settings.model_network]
    asyncio.run(scenario())


def test_coordinator_policy_loads_and_routes_provider_origins_independently(tmp_path, monkeypatch):
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from naiyue_test_agent.application.cloud.settings import CloudSettings
    from naiyue_test_agent.application.cloud.model_relay import ModelRelay
    from model_proxy_helpers import model_settings
    async def scenario():
        proxied, direct = [], []
        async with endpoint('openai', proxied) as proxy, endpoint('openai', direct) as target:
            config, _ = model_settings(tmp_path, upstream=target)
            data = config.model_dump()
            data['model_network'] = dict(mode='proxy', proxy=proxy, no_proxy=['127.0.0.1'])
            data['model_proxy']['upstreams']['o'] = 'http://10.0.0.9/v1'
            config = CloudSettings.model_validate(data)
            assert config.model_network.mode == 'proxy'
            with open_cloud_runtime(config) as runtime:
                relay = runtime.models.relay = ModelRelay(runtime.models)
                client = relay.http()
                assert relay.http() is client
                try:
                    for url in ('http://10.0.0.9/v1/chat/completions', target + '/v1/chat/completions'):
                        response = await client.post(url, json={'model': 'synthetic', 'messages': []})
                        assert response.status_code == 200
                    assert len(proxied) == len(direct) == 1
                finally:
                    await runtime.models.aclose()
                assert client.is_closed
    asyncio.run(scenario())


@pytest.mark.parametrize('module_name', ['httpx', 'httpx2'])
def test_explicit_route_keeps_environment_ca_and_certificate_verification(monkeypatch, tmp_path, module_name):
    import importlib
    import ssl
    import certifi
    from naiyue_test_agent.model_access.network import client_options
    module = importlib.import_module(module_name)
    policy = NetworkSettings(mode='proxy', proxy='https://proxy.invalid')
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'missing-ca.pem'))
    with pytest.raises(FileNotFoundError):
        client_options('https://model.invalid', policy, http_module=module)
    monkeypatch.setenv('SSL_CERT_FILE', certifi.where())
    options = client_options('https://model.invalid', policy, http_module=module)
    for context in (options['verify'], options['proxy'].ssl_context):
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert context.cert_store_stats()['x509_ca'] > 0
    assert options['trust_env'] is False


def test_coordinator_omitted_network_policy_keeps_environment_proxy(tmp_path, monkeypatch):
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from naiyue_test_agent.application.cloud.model_relay import ModelRelay
    from model_proxy_helpers import model_settings
    async def scenario():
        proxied, direct = [], []
        async with endpoint('openai', proxied) as proxy, endpoint('openai', direct) as target:
            for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
                monkeypatch.delenv(key, raising=False)
            monkeypatch.setenv('HTTP_PROXY', proxy)
            config, _ = model_settings(tmp_path, upstream=target)
            assert 'model_network' not in config.model_fields_set
            assert config.model_network.mode == 'auto' and config.model_network.no_proxy == ()
            with open_cloud_runtime(config) as runtime:
                relay = runtime.models.relay = ModelRelay(runtime.models)
                client = relay.http()
                try:
                    response = await client.post(target + '/v1/chat/completions',
                                                 json={'model': 'synthetic', 'messages': []})
                    assert response.status_code == 200 and len(proxied) == 1 and not direct
                finally:
                    await runtime.models.aclose()
                assert client.is_closed
    asyncio.run(scenario())
