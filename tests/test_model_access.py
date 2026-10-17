
from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.application import prepare_tool_registry
import json
from pathlib import Path

import anthropic
import httpx
import httpx2
import ollama
import openai
from ollama import ResponseError
import pytest

from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from naiyue_test_agent.agent_runtime import build_configured_runtime_agent
from naiyue_test_agent.model_access import ModelConfigError, load_model_catalog
from naiyue_test_agent.model_access.errors import classify_model_failure
from naiyue_test_agent.model_access.thinking import resolve_thinking_level


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / 'models.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    path.chmod(0o600)
    return path


def anthropic_status_error(error_type, status_code):
    request = httpx.Request('POST', 'https://example.invalid/messages')
    response = httpx.Response(status_code, request=request)
    return error_type('synthetic private response', response=response, body=None)


@pytest.mark.parametrize(('error', 'expected'), [
    (anthropic_status_error(anthropic.AuthenticationError, 401),
     ('access_denied', 'authentication_failed')),
    (anthropic_status_error(anthropic.PermissionDeniedError, 403),
     ('access_denied', 'permission_denied')),
    (anthropic.APITimeoutError(httpx.Request('POST', 'https://example.invalid/messages')),
     ('transient', 'timeout')),
    (anthropic.APIConnectionError(
        request=httpx.Request('POST', 'https://example.invalid/messages')),
     ('transient', 'connection_error')),
    (anthropic_status_error(anthropic.NotFoundError, 404),
     ('request_rejected', 'not_found')),
    (anthropic_status_error(anthropic.RateLimitError, 429),
     ('limit_exceeded', 'limit_unspecified')),
    (anthropic_status_error(anthropic.APIStatusError, 529),
     ('transient', 'server_error')),
    (anthropic_status_error(anthropic.BadRequestError, 400),
     ('unknown', 'unclassified')),
    (ResponseError('synthetic private response', 400),
     ('request_rejected', 'bad_request')),
    (ResponseError('synthetic private response', 401),
     ('access_denied', 'authentication_failed')),
    (ResponseError('synthetic private response', 403),
     ('access_denied', 'permission_denied')),
    (ResponseError('synthetic private response', 404),
     ('request_rejected', 'not_found')),
    (ResponseError('synthetic private response', 429),
     ('limit_exceeded', 'limit_unspecified')),
    (ResponseError('synthetic private response', 503),
     ('transient', 'server_error')),
    (ResponseError('synthetic private response'),
     ('unknown', 'unclassified')),
    (ResponseError('synthetic private response', 418),
     ('unknown', 'unclassified')),
    (ConnectionError('synthetic private response'),
     ('transient', 'connection_error')),
    (httpx.ReadTimeout('synthetic private response'),
     ('transient', 'timeout')),
])
def test_non_openai_sdk_failures_have_safe_coarse_classification(error, expected):
    assert classify_model_failure(error) == expected


def test_anthropic_sdk_401_is_classified_through_mock_transport():
    def reject(_request):
        return httpx2.Response(401, json={
            'type': 'error', 'error': {'type': 'authentication_error', 'message': 'synthetic'},
        })

    with httpx2.Client(transport=httpx2.MockTransport(reject)) as http_client:
        client = anthropic.Anthropic(api_key='synthetic-key', http_client=http_client,
                                     max_retries=0)
        with pytest.raises(anthropic.AuthenticationError) as caught:
            client.messages.create(model='synthetic-model', max_tokens=1,
                                   messages=[{'role': 'user', 'content': 'probe'}])

    assert classify_model_failure(caught.value) == ('access_denied', 'authentication_failed')


def test_ollama_sdk_404_is_classified_through_mock_transport():
    def reject(_request):
        return httpx.Response(404, json={'error': 'synthetic model missing'})

    with ollama.Client(host='http://example.invalid',
                       transport=httpx.MockTransport(reject)) as client:
        with pytest.raises(ResponseError) as caught:
            client.chat(model='synthetic-model',
                        messages=[{'role': 'user', 'content': 'probe'}])

    assert classify_model_failure(caught.value) == ('request_rejected', 'not_found')


def config_for_all_kinds():
    return {
        'schema_version': 1,
        'default_model_id': 'gateway',
        'providers': {
            'official': {'kind': 'openai', 'api_key': 'openai-test-key'},
            'gateway': {'kind': 'openai_compatible', 'base_url': 'https://example.test/v1',
                        'api_key': 'gateway-test-key'},
            'claude': {'kind': 'anthropic', 'api_key': 'anthropic-test-key'},
            'local': {'kind': 'ollama', 'base_url': 'http://127.0.0.1:11434'},
        },
        'models': {
            'official': {'provider_id': 'official', 'model': 'openai-test-model', 'api': 'chat_completions'},
            'gateway': {'provider_id': 'gateway', 'model': 'gateway-test-model', 'api': 'chat_completions'},
            'claude': {'provider_id': 'claude', 'model': 'claude-test-model'},
            'local': {'provider_id': 'local', 'model': 'local-test-model'},
        },
    }


def test_loads_all_model_kinds_without_network(tmp_path):
    from langchain_anthropic import ChatAnthropic
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI

    catalog = load_model_catalog(write_config(tmp_path, config_for_all_kinds()))

    assert catalog.default_model_id == 'gateway'
    assert isinstance(catalog.get('official'), ChatOpenAI)
    assert isinstance(catalog.get('gateway'), ChatOpenAI)
    assert isinstance(catalog.get('claude'), ChatAnthropic)
    assert isinstance(catalog.get('local'), ChatOllama)
    assert catalog.get('gateway').openai_api_base == 'https://example.test/v1'
    with pytest.raises(ModelConfigError, match='missing-model'):
        catalog.get('missing-model')


def test_request_policy_defaults_reach_all_adapters_without_new_config(tmp_path):
    catalog = load_model_catalog(write_config(tmp_path, config_for_all_kinds()))

    for model_id in ('official', 'gateway'):
        model = catalog.get(model_id)
        assert model.request_timeout == 60
        assert model.max_retries == 2
    assert catalog.get('claude').default_request_timeout == 60
    assert catalog.get('claude').max_retries == 2
    assert catalog.get('local').client_kwargs['timeout'] == 60


def test_request_policy_overrides_one_field_without_losing_other_default(tmp_path):
    config = config_for_all_kinds()
    config['models']['gateway']['request'] = {'timeout_seconds': 90, 'sdk_max_retries': 0}
    config['models']['claude']['request'] = {'sdk_max_retries': 1}
    config['models']['local']['request'] = {'timeout_seconds': 120}

    catalog = load_model_catalog(write_config(tmp_path, config))

    assert catalog.get('gateway').request_timeout == 90
    assert catalog.get('gateway').max_retries == 0
    assert catalog.get('claude').default_request_timeout == 60
    assert catalog.get('claude').max_retries == 1
    assert catalog.get('local').client_kwargs['timeout'] == 120


@pytest.mark.parametrize(('request_policy', 'message'), [
    ({'timeout_seconds': 0}, 'timeout_seconds'),
    ({'timeout_seconds': -1}, 'timeout_seconds'),
    ({'timeout_seconds': True}, 'timeout_seconds'),
    ({'timeout_seconds': float('inf')}, 'timeout_seconds'),
    ({'sdk_max_retries': -1}, 'sdk_max_retries'),
    ({'sdk_max_retries': 1.5}, 'sdk_max_retries'),
    ({'sdk_max_retries': True}, 'sdk_max_retries'),
    ({'unknown': 1}, 'unknown'),
])
def test_invalid_request_policy_fails_before_network_without_leaking_key(
    tmp_path, request_policy, message,
):
    config = config_for_all_kinds()
    config['providers']['gateway']['api_key'] = 'DO_NOT_LEAK_THIS_KEY'
    config['models']['gateway']['request'] = request_policy

    with pytest.raises(ModelConfigError, match=message) as caught:
        load_model_catalog(write_config(tmp_path, config))
    assert 'DO_NOT_LEAK_THIS_KEY' not in str(caught.value)


def test_ollama_explicit_sdk_retries_are_rejected(tmp_path):
    config = config_for_all_kinds()
    config['models']['local']['request'] = {'sdk_max_retries': 2}

    with pytest.raises(ModelConfigError, match='sdk_max_retries'):
        load_model_catalog(write_config(tmp_path, config))


@pytest.mark.parametrize(('retry_count', 'expected_attempts'), [(None, 3), (0, 1)])
def test_configured_openai_sdk_retries_only_at_its_boundary(
    tmp_path, monkeypatch, retry_count, expected_attempts,
):
    from langchain_openai import ChatOpenAI

    attempts = []

    def reject(request):
        attempts.append(request)
        return httpx2.Response(503, json={'error': {'message': 'synthetic failure'}})

    config = config_for_all_kinds()
    if retry_count is not None:
        config['models']['gateway']['request'] = {'sdk_max_retries': retry_count}

    with httpx2.Client(transport=httpx2.MockTransport(reject)) as http_client:
        monkeypatch.setattr(
            'naiyue_test_agent.model_access.ChatOpenAI',
            lambda **kwargs: ChatOpenAI(http_client=http_client, **kwargs),
        )
        catalog = load_model_catalog(write_config(tmp_path, config))
        with pytest.raises(openai.InternalServerError):
            catalog.get('gateway').invoke([HumanMessage(content='synthetic probe')])

    assert len(attempts) == expected_attempts


@pytest.mark.parametrize(('retry_count', 'expected_attempts'), [(None, 3), (0, 1)])
def test_configured_anthropic_sdk_retries_only_at_its_boundary(
    tmp_path, monkeypatch, retry_count, expected_attempts,
):
    attempts = []

    def reject(request):
        attempts.append(request)
        return httpx2.Response(503, json={
            'type': 'error', 'error': {'type': 'api_error', 'message': 'synthetic failure'},
        })

    config = config_for_all_kinds()
    if retry_count is not None:
        config['models']['claude']['request'] = {'sdk_max_retries': retry_count}

    with httpx2.Client(transport=httpx2.MockTransport(reject)) as http_client:
        monkeypatch.setattr(
            'langchain_anthropic.chat_models._get_default_httpx_client',
            lambda **_kwargs: http_client,
        )
        catalog = load_model_catalog(write_config(tmp_path, config))
        with pytest.raises(anthropic.APIStatusError):
            catalog.get('claude').invoke([HumanMessage(content='synthetic probe')])

    assert len(attempts) == expected_attempts


def test_thinking_level_prefers_next_higher_then_lower():
    assert resolve_thinking_level('high', ('off', 'low', 'xhigh')) == 'xhigh'
    assert resolve_thinking_level('high', ('off', 'low')) == 'low'
    assert resolve_thinking_level('model_default', ('off', 'low')) == 'model_default'


def test_thinking_defaults_do_not_change_existing_model_parameters(tmp_path):
    config = config_for_all_kinds()
    config['models']['official']['thinking'] = {
        'supported_levels': ['off', 'high'], 'default_level': 'model_default'}
    catalog = load_model_catalog(write_config(tmp_path, config))

    assert catalog.get('official').reasoning_effort is None
    assert catalog.get('gateway').reasoning_effort is None
    assert catalog.get('claude').reasoning_effort is None
    assert catalog.get('local').reasoning is None
    selection = catalog.thinking_selection('official')
    assert (selection.requested, selection.selected, selection.native_settings) == (
        'model_default', 'model_default', {})


def test_thinking_configuration_reaches_each_provider_adapter(tmp_path):
    config = config_for_all_kinds()
    config['models']['official']['thinking'] = {
        'supported_levels': ['off', 'low', 'xhigh'], 'default_level': 'high'}
    config['models']['gateway']['thinking'] = {
        'supported_levels': ['off', 'low'], 'default_level': 'off'}
    config['models']['claude']['thinking'] = {
        'supported_levels': ['off', 'low', 'high'], 'default_level': 'high'}
    config['models']['local']['thinking'] = {
        'supported_levels': ['off', 'low', 'medium', 'high'], 'default_level': 'medium'}

    catalog = load_model_catalog(write_config(tmp_path, config))

    assert catalog.get('official').reasoning_effort == 'xhigh'
    assert catalog.thinking_selection('official').requested == 'high'
    assert catalog.thinking_selection('official').selected == 'xhigh'
    assert catalog.thinking_selection('official').native_settings == {'reasoning_effort': 'xhigh'}
    assert catalog.get('official')._get_request_payload(
        [HumanMessage(content='synthetic probe')])['reasoning_effort'] == 'xhigh'
    assert catalog.get('gateway').reasoning_effort == 'none'
    assert catalog.get('claude').reasoning_effort == 'high'
    assert catalog.get('local').reasoning == 'medium'


def test_anthropic_off_maps_to_disabled_thinking(tmp_path):
    config = config_for_all_kinds()
    config['models']['claude']['thinking'] = {
        'supported_levels': ['off', 'low'], 'default_level': 'off'}

    catalog = load_model_catalog(write_config(tmp_path, config))

    assert catalog.get('claude').thinking == {'type': 'disabled'}


@pytest.mark.parametrize(('thinking', 'message'), [
    ({'supported_levels': ['low', 'low'], 'default_level': 'low'}, 'supported_levels'),
    ({'supported_levels': ['low', 'ultra'], 'default_level': 'low'}, 'supported_levels'),
    ({'supported_levels': [], 'default_level': 'low'}, 'supported_levels'),
    ({'supported_levels': ['low'], 'default_level': 'ultra'}, 'default_level'),
    ({'supported_levels': ['low']}, 'default_level'),
    ({'supported_levels': ['low'], 'default_level': 'low', 'extra': True}, 'extra'),
])
def test_invalid_thinking_configuration_is_rejected(tmp_path, thinking, message):
    config = config_for_all_kinds()
    config['models']['official']['thinking'] = thinking

    with pytest.raises(ModelConfigError, match=message):
        load_model_catalog(write_config(tmp_path, config))


def test_unsupported_native_thinking_level_is_rejected(tmp_path):
    config = config_for_all_kinds()
    config['models']['claude']['thinking'] = {
        'supported_levels': ['minimal', 'low'], 'default_level': 'minimal'}

    with pytest.raises(ModelConfigError, match='minimal'):
        load_model_catalog(write_config(tmp_path, config))


@pytest.mark.parametrize('level', ['minimal', 'xhigh'])
def test_ollama_rejects_undocumented_thinking_levels(tmp_path, level):
    config = config_for_all_kinds()
    config['models']['local']['thinking'] = {
        'supported_levels': [level], 'default_level': level}

    with pytest.raises(ModelConfigError, match=level):
        load_model_catalog(write_config(tmp_path, config))


def test_ollama_max_thinking_level_reaches_adapter(tmp_path):
    config = config_for_all_kinds()
    config['models']['local']['thinking'] = {
        'supported_levels': ['max'], 'default_level': 'max'}

    catalog = load_model_catalog(write_config(tmp_path, config))

    assert catalog.get('local').reasoning == 'max'
    assert catalog.thinking_selection('local').selected == 'max'


@pytest.mark.parametrize(('change', 'message'), [
    (lambda c: c.update(schema_version=2), 'schema_version'),
    (lambda c: c.update(default_model_id='missing'), 'default_model_id'),
    (lambda c: c['models']['gateway'].update(provider_id='missing'), 'provider_id'),
    (lambda c: c['providers']['gateway'].pop('api_key'), 'api_key'),
    (lambda c: c['providers']['gateway'].update(base_url='file:///tmp/local'), 'base_url'),
    (lambda c: c['providers']['gateway'].update(base_url='https://example.test:bad'), 'base_url'),
    (lambda c: c['providers']['gateway'].update(kind=[]), 'kind'),
    (lambda c: c['providers']['gateway'].update(extra='x'), 'extra'),
])
def test_invalid_config_fails_without_leaking_key(tmp_path, change, message):
    config = config_for_all_kinds()
    config['providers']['official']['api_key'] = 'DO_NOT_LEAK_THIS_KEY'
    change(config)

    with pytest.raises(ModelConfigError, match=message) as exc:
        load_model_catalog(write_config(tmp_path, config))
    assert 'DO_NOT_LEAK_THIS_KEY' not in str(exc.value)


def test_duplicate_json_key_is_rejected(tmp_path):
    path = tmp_path / 'models.json'
    path.write_text('{"schema_version": 1, "schema_version": 2}', encoding='utf-8')
    path.chmod(0o600)
    with pytest.raises(ModelConfigError, match='duplicate'):
        load_model_catalog(path)


def test_missing_config_is_rejected(tmp_path):
    with pytest.raises(ModelConfigError, match='models.json'):
        load_model_catalog(tmp_path / 'models.json')


def test_configured_agent_uses_file_and_switches_after_tool_result(tmp_path, monkeypatch):
    selected = {'s1': 'first'}
    received_settings = {}
    models = {
        'model-first': ToolCapableFake(responses=[AIMessage(
            content='', tool_calls=[{'name': 'switch_to_second', 'args': {}, 'id': 'call-1'}]
        )]),
        'model-second': ToolCapableFake(responses=[AIMessage(content='answer-from-second')]),
    }
    def fake_chat_openai(*, model, **kwargs):
        received_settings[model] = kwargs
        return models[model]

    monkeypatch.setattr('naiyue_test_agent.model_access.ChatOpenAI', fake_chat_openai)
    config = {
        'schema_version': 1,
        'default_model_id': 'first',
        'providers': {'p': {'kind': 'openai', 'api_key': 'test-key'}},
        'models': {
            'first': {'provider_id': 'p', 'model': 'model-first', 'api': 'chat_completions',
                      'thinking': {'supported_levels': ['off', 'low', 'high'],
                                   'default_level': 'high'}},
            'second': {'provider_id': 'p', 'model': 'model-second', 'api': 'chat_completions'},
        },
    }
    selections = []

    @tool
    def switch_to_second() -> str:
        """Select the second configured model for this test session."""
        selected['s1'] = 'second'
        return 'selected second'

    _runtime_registry_1 = prepare_tool_registry([ToolSpec(switch_to_second, switch_to_second.args_schema, permissions=ToolPermission(default='allow'))])
    _runtime_names_1 = _runtime_registry_1.names()
    agent = build_configured_runtime_agent(
        write_config(tmp_path, config),
        tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='Test model selection.', response_format=None,
        select_model_id=lambda session_id: selected[session_id],
        record_selection=lambda session_id, model_id: selections.append((session_id, model_id)),
    )
    result = agent.invoke({'messages': [('user', 'switch, then answer')]}, context={'session_id': 's1'})

    assert selections == [('s1', 'first'), ('s1', 'second')]
    assert [message.type for message in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert result['messages'][2].tool_call_id == result['messages'][1].tool_calls[0]['id']
    assert result['messages'][-1].content == 'answer-from-second'
    assert received_settings['model-first']['reasoning_effort'] == 'high'
    assert received_settings['model-first']['timeout'] == 60
    assert received_settings['model-first']['max_retries'] == 2
    assert 'reasoning_effort' not in received_settings['model-second']
    assert received_settings['model-second']['timeout'] == 60
    assert received_settings['model-second']['max_retries'] == 2


def test_configured_agent_forwards_checkpointer_for_continuous_messages(tmp_path, monkeypatch):
    model = FakeMessagesListChatModel(responses=[
        AIMessage(content='first answer'), AIMessage(content='second answer'),
    ])
    monkeypatch.setattr('naiyue_test_agent.model_access.ChatOpenAI', lambda **_kwargs: model)
    config_path = write_config(tmp_path, {
        'schema_version': 1,
        'default_model_id': 'first',
        'providers': {'p': {'kind': 'openai', 'api_key': 'test-key'}},
        'models': {'first': {'provider_id': 'p', 'model': 'model-first', 'api': 'chat_completions'}},
    })
    _runtime_registry_2 = prepare_tool_registry([])
    _runtime_names_2 = _runtime_registry_2.names()
    agent = build_configured_runtime_agent(
        config_path, tool_registry=_runtime_registry_2, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_2: names, system_prompt='', response_format=None,
        select_model_id=lambda _session_id: 'first',
        record_selection=lambda _session_id, _model_id: None,
        checkpointer=InMemorySaver(),
    )
    invoke_options = {
        'context': {'session_id': 's1'},
        'config': {'configurable': {'thread_id': 'internal-thread-s1'}},
    }

    agent.invoke({'messages': [('user', 'first question')]}, **invoke_options)
    result = agent.invoke({'messages': [('user', 'second question')]}, **invoke_options)

    assert [message.content for message in result['messages']] == [
        'first question', 'first answer', 'second question', 'second answer',
    ]
