"""Configured context budgets remain visible through the real agent call path."""

from native_application_helpers import async_test

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.application import prepare_tool_registry

import json
from contextlib import AsyncExitStack
import logging
from types import SimpleNamespace

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from naiyue_test_agent.agent_runtime import build_runtime_agent
from naiyue_test_agent.agent_runtime.context_observation import observe_model_request
from naiyue_test_agent.model_access import (
    ModelConfigError, bind_model_output_limit, load_model_catalog,
)


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _config(tmp_path, *, first_limits=None, second_limits=None):
    data = {
        'schema_version': 1, 'default_model_id': 'first',
        'providers': {'p': {'kind': 'openai', 'api_key': 'synthetic-key'}},
        'models': {
            'first': {'provider_id': 'p', 'model': 'synthetic-first', 'api': 'chat_completions', **(first_limits or {})},
            'second': {'provider_id': 'p', 'model': 'synthetic-second', 'api': 'chat_completions', **(second_limits or {})},
        },
    }
    path = tmp_path / 'models.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    path.chmod(0o600)
    return path


def test_default_limits_reach_each_adapter_and_are_marked_as_defaults(tmp_path):
    from langchain_anthropic import ChatAnthropic
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI

    data = json.loads(_config(tmp_path).read_text())
    data['providers'].update({
        'claude': {'kind': 'anthropic', 'api_key': 'synthetic-key'},
        'local': {'kind': 'ollama', 'base_url': 'http://127.0.0.1:11434'},
        'gateway': {'kind': 'openai_compatible', 'api_key': 'synthetic-key',
                    'base_url': 'https://example.test/v1'},
    })
    data['models'].update({
        'claude': {'provider_id': 'claude', 'model': 'synthetic-claude'},
        'local': {'provider_id': 'local', 'model': 'synthetic-local'},
        'gateway': {'provider_id': 'gateway', 'model': 'synthetic-gateway', 'api': 'chat_completions'},
    })
    path = tmp_path / 'models.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    catalog = load_model_catalog(path)

    for model_id in ('first', 'gateway'):
        model = catalog.get(model_id)
        assert isinstance(model, ChatOpenAI)
        assert model.max_tokens == 16384
    assert isinstance(catalog.get('claude'), ChatAnthropic)
    assert catalog.get('claude').max_tokens == 16384
    assert isinstance(catalog.get('local'), ChatOllama)
    assert catalog.get('local').num_predict == 16384
    for model_id in ('first', 'gateway', 'claude', 'local'):
        assert catalog.get(model_id).metadata['naiyue_model_limits'] == {
            'context_window_tokens': 128000, 'max_output_tokens': 16384,
            'context_window_source': 'default', 'max_output_source': 'default',
            'model_id': model_id,
        }


def test_limits_overrides_are_per_model_and_must_be_positive_integers(tmp_path):
    catalog = load_model_catalog(_config(
        tmp_path, first_limits={'context_window_tokens': 65536, 'max_output_tokens': 8192},
    ))
    assert catalog.get('first').max_tokens == 8192
    assert catalog.get('first').metadata['naiyue_model_limits'] == {
        'context_window_tokens': 65536, 'max_output_tokens': 8192,
        'context_window_source': 'config', 'max_output_source': 'config',
        'model_id': 'first',
    }
    assert catalog.get('second').max_tokens == 16384
    for field, value in (
        ('context_window_tokens', 0), ('context_window_tokens', True),
        ('context_window_tokens', 1.5), ('max_output_tokens', -1),
        ('max_output_tokens', '16384'),
    ):
        with pytest.raises(ModelConfigError, match=field) as caught:
            load_model_catalog(_config(tmp_path, first_limits={field: value}))
        assert 'synthetic-key' not in str(caught.value)


@async_test
async def test_budget_observation_follows_model_switch_and_missing_usage(tmp_path, monkeypatch, caplog):
    selected = {'s1': 'first'}
    models = {
        'synthetic-first': ToolCapableFake(responses=[AIMessage(
            content='', tool_calls=[{'name': 'switch_model', 'args': {}, 'id': 'call-1'}],
            usage_metadata={'input_tokens': 110, 'output_tokens': 2, 'total_tokens': 112},
        )]),
        'synthetic-second': ToolCapableFake(responses=[AIMessage(content='done')]),
    }

    def fake_openai(*, model, metadata, **_kwargs):
        models[model].metadata = metadata
        return models[model]

    monkeypatch.setattr('naiyue_test_agent.model_access.ChatOpenAI', fake_openai)

    @tool
    def switch_model() -> str:
        """Switch the session model."""
        selected['s1'] = 'second'
        return 'switched'

    _runtime_registry_1 = prepare_tool_registry([ToolSpec(switch_model, switch_model.args_schema, permissions=ToolPermission(default='allow'))])
    _runtime_names_1 = _runtime_registry_1.names()
    async with AsyncExitStack() as resources:
        catalog = load_model_catalog(_config(
            tmp_path, first_limits={'context_window_tokens': 120, 'max_output_tokens': 16},
            second_limits={'context_window_tokens': 200, 'max_output_tokens': 32}))
        resources.push_async_callback(catalog.aclose)
        agent = build_runtime_agent(
            catalog.get('first'), tool_registry=_runtime_registry_1,
            resolve_active_tool_names=lambda _sid, agent_path='root': _runtime_names_1,
            system_prompt='BASE', response_format=None,
            resolve_model=lambda sid, agent_path='root': (selected[sid], catalog.get(selected[sid])))
        with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
            await agent.ainvoke({'messages': [('user', 'SECRET_REQUEST')]}, context={'session_id': 's1'})

    events = [record.model_context_budget for record in caplog.records
              if hasattr(record, 'model_context_budget')]
    assert len(events) == 2
    assert events[0] == {
        'event': 'model_context_budget', 'model_id': 'first',
        'session_id': 's1', 'run_id': None,
        'context_window_tokens': 120, 'max_output_tokens': 16,
        'input_budget_tokens': 104, 'input_tokens': 110,
        'threshold_exceeded': True,
        'context_window_source': 'config', 'max_output_source': 'config',
        'outcome': 'returned',
    }
    assert events[1]['context_window_tokens'] == 200
    assert events[1]['model_id'] == 'second'
    assert events[1]['max_output_tokens'] == 32
    assert events[1]['input_budget_tokens'] == 168
    assert events[1]['input_tokens'] is None
    assert events[1]['threshold_exceeded'] is None
    preflights = [record.model_context_preflight for record in caplog.records
                  if hasattr(record, 'model_context_preflight')]
    assert [event['model_id'] for event in preflights] == ['first', 'second']
    assert [event['context_window_tokens'] for event in preflights] == [120, 200]
    assert [event['compaction_reserve_tokens'] for event in preflights] == [30, 50]
    assert all(event['estimate_status'] == 'available' for event in preflights)
    assert 'SECRET_REQUEST' not in caplog.text


@async_test
async def test_budget_observation_on_model_error_preserves_failure(tmp_path, monkeypatch, caplog):
    failure = RuntimeError('SECRET_PROVIDER_FAILURE')

    class FailingModel(ToolCapableFake):
        def _generate(self, *_args, **_kwargs):
            raise failure

    def fake_openai(*, metadata, **_kwargs):
        return FailingModel(responses=[AIMessage(content='unused')], metadata=metadata)

    monkeypatch.setattr('naiyue_test_agent.model_access.ChatOpenAI', fake_openai)
    _runtime_registry_2 = prepare_tool_registry([])
    _runtime_names_2 = _runtime_registry_2.names()
    async with AsyncExitStack() as resources:
        catalog = load_model_catalog(_config(tmp_path))
        resources.push_async_callback(catalog.aclose)
        agent = build_runtime_agent(
            catalog.get('first'), tool_registry=_runtime_registry_2,
            resolve_active_tool_names=lambda _sid, agent_path='root': _runtime_names_2,
            system_prompt='', response_format=None,
            resolve_model=lambda _sid, agent_path='root': ('first', catalog.get('first')))
        with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
            with pytest.raises(RuntimeError) as caught:
                await agent.ainvoke({'messages': [('user', 'SECRET_REQUEST')]}, context={'session_id': 's1'})

    assert caught.value is failure
    budgets = [record.model_context_budget for record in caplog.records
               if hasattr(record, 'model_context_budget')]
    assert len(budgets) == 1
    assert budgets[0]['model_id'] == 'first'
    assert budgets[0]['input_tokens'] is None
    assert budgets[0]['threshold_exceeded'] is None
    assert budgets[0]['outcome'] == 'failed'
    assert 'SECRET_REQUEST' not in caplog.text
    assert 'SECRET_PROVIDER_FAILURE' not in caplog.text


def test_compaction_reserve_default_override_and_invalid_config(tmp_path):
    catalog = load_model_catalog(_config(
        tmp_path, first_limits={'context_window_tokens': 128000},
        second_limits={'context_window_tokens': 32000,
                       'compaction_reserve_tokens': 4000},
    ))
    assert catalog.get('first').metadata['naiyue_compaction_reserve'] == {
        'tokens': 16384, 'source': 'default',
    }
    assert catalog.get('second').metadata['naiyue_compaction_reserve'] == {
        'tokens': 4000, 'source': 'config',
    }
    small = load_model_catalog(_config(
        tmp_path, first_limits={'context_window_tokens': 120,
                                'max_output_tokens': 16},
    ))
    assert small.get('first').metadata['naiyue_compaction_reserve']['tokens'] == 30
    for value in (0, True, -1, '200'):
        with pytest.raises(ModelConfigError, match='compaction_reserve_tokens'):
            load_model_catalog(_config(tmp_path, first_limits={
                'compaction_reserve_tokens': value,
            }))


@async_test
@pytest.mark.parametrize(('estimated_tokens', 'output_limit', 'expected_threshold',
                          'needs_compaction'), [
    (80, 10, 80, False),
    (81, 10, 80, True),
    (71, 30, 70, True),
])
async def test_preflight_uses_final_request_and_does_not_change_model_call(
    monkeypatch, caplog, estimated_tokens, output_limit, expected_threshold,
    needs_compaction,
):
    seen = []

    def estimate(messages, *, tools):
        seen.append((messages, tools))
        return estimated_tokens

    monkeypatch.setattr('naiyue_test_agent.agent_runtime.context_observation'
                        '.count_tokens_approximately', estimate)
    request = SimpleNamespace(
        runtime=SimpleNamespace(context={'session_id': 's1', 'run_id': 'r1'}),
        model=SimpleNamespace(metadata={
            'naiyue_model_limits': {
                'model_id': 'A', 'context_window_tokens': 100,
                'max_output_tokens': output_limit, 'context_window_source': 'config',
            },
            'naiyue_compaction_reserve': {'tokens': 20, 'source': 'config'},
        }),
        messages=[HumanMessage(content='SECRET_REQUEST')],
        system_message=SystemMessage(content='SECRET_SYSTEM'),
        tools=[{'name': 'read', 'description': 'SECRET_TOOL'}],
    )
    response = SimpleNamespace(result=[AIMessage(content='done')])
    handled = []

    async def handler(received):
        handled.append(received)
        return response

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        assert (await observe_model_request.awrap_model_call(request, handler)) is response
    assert handled == [request]
    assert len(seen) == 1
    assert seen[0][0] == [request.system_message, *request.messages]
    assert seen[0][1] is request.tools
    events = [record.model_context_preflight for record in caplog.records
              if hasattr(record, 'model_context_preflight')]
    assert events == [{
        'event': 'model_context_preflight', 'model_id': 'A',
        'session_id': 's1', 'run_id': 'r1',
        'context_window_tokens': 100, 'context_window_source': 'config',
        'max_output_tokens': output_limit, 'compaction_reserve_tokens': 20,
        'compaction_reserve_source': 'config',
        'effective_headroom_tokens': max(output_limit, 20),
        'trigger_threshold_tokens': expected_threshold,
        'estimated_input_tokens': estimated_tokens,
        'needs_compaction': needs_compaction, 'estimate_status': 'available',
    }]
    assert 'SECRET_REQUEST' not in caplog.text
    assert 'SECRET_SYSTEM' not in caplog.text
    assert 'SECRET_TOOL' not in caplog.text


@async_test
async def test_preflight_estimation_failure_is_visible_and_does_not_block(monkeypatch, caplog):
    async def async_handler_1(_request):
        return response
    def fail_estimate(*_args, **_kwargs):
        raise RuntimeError('SECRET_ESTIMATION_FAILURE')

    monkeypatch.setattr('naiyue_test_agent.agent_runtime.context_observation'
                        '.count_tokens_approximately', fail_estimate)
    request = SimpleNamespace(
        runtime=SimpleNamespace(context={'session_id': 's1'}),
        model=SimpleNamespace(metadata={
            'naiyue_model_limits': {
                'model_id': 'A', 'context_window_tokens': 100,
                'max_output_tokens': 10, 'context_window_source': 'config',
            },
            'naiyue_compaction_reserve': {'tokens': 20, 'source': 'default'},
        }),
        messages=[HumanMessage(content='SECRET_REQUEST')],
        system_message=None, tools=[],
    )
    response = SimpleNamespace(result=[AIMessage(content='done')])
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        assert (await observe_model_request.awrap_model_call(request, async_handler_1)) is response
    events = [record.model_context_preflight for record in caplog.records
              if hasattr(record, 'model_context_preflight')]
    assert len(events) == 1
    assert events[0]['estimate_status'] == 'unavailable'
    assert events[0]['needs_compaction'] is None
    assert events[0]['estimated_input_tokens'] is None
    assert 'context_preflight_estimate_unavailable error_type=RuntimeError' in caplog.text
    assert 'SECRET_ESTIMATION_FAILURE' not in caplog.text


def test_recent_context_keep_default_override_and_invalid_config(tmp_path):
    catalog = load_model_catalog(_config(
        tmp_path,
        first_limits={'context_window_tokens': 128000},
        second_limits={'context_window_tokens': 32000,
                       'compaction_keep_recent_tokens': 5000},
    ))
    assert catalog.get('first').metadata['naiyue_compaction_keep_recent'] == {
        'tokens': 20000, 'source': 'default',
    }
    assert catalog.get('second').metadata['naiyue_compaction_keep_recent'] == {
        'tokens': 5000, 'source': 'config',
    }
    small = load_model_catalog(_config(
        tmp_path, first_limits={'context_window_tokens': 120},
    ))
    assert small.get('first').metadata['naiyue_compaction_keep_recent']['tokens'] == 30
    for value in (0, True, -1, '200'):
        with pytest.raises(ModelConfigError, match='compaction_keep_recent_tokens'):
            load_model_catalog(_config(tmp_path, first_limits={
                'compaction_keep_recent_tokens': value,
            }))


def test_summary_output_cap_default_override_and_invalid_config(tmp_path):
    catalog = load_model_catalog(_config(
        tmp_path,
        first_limits={'compaction_summary_output_tokens': 512},
    ))
    assert catalog.get('first').metadata['naiyue_compaction_summary_output'] == {
        'tokens': 512, 'source': 'config',
    }
    assert catalog.get('second').metadata['naiyue_compaction_summary_output'] == {
        'tokens': 2048, 'source': 'default',
    }
    for value in (0, True, -1, '512', 20000):
        with pytest.raises(ModelConfigError, match='compaction_summary_output_tokens'):
            load_model_catalog(_config(tmp_path, first_limits={
                'compaction_summary_output_tokens': value,
            }))


def test_summary_output_cap_uses_each_adapter_parameter():
    from langchain_anthropic import ChatAnthropic
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI

    models = (
        (ChatOpenAI(model='synthetic', api_key='synthetic-key'), 'max_completion_tokens'),
        (ChatAnthropic(model='synthetic', api_key='synthetic-key'), 'max_tokens'),
        (ChatOllama(model='synthetic', base_url='http://127.0.0.1:11434'),
         'num_predict'),
    )
    for model, key in models:
        capped = bind_model_output_limit(model, 256)
        if isinstance(model, ChatOllama):
            assert capped.bound.num_predict == 256 and capped.kwargs == {}
            assert model.num_predict is None
        else:
            assert capped.kwargs == {key: 256}
