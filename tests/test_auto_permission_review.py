"""Auto review sends bounded source-aware data through two distinct stages."""

from native_application_helpers import async_test

import json
import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.agent_runtime.tool_execution.permissions import (
    AutoPermissionReviewer, collect_human_messages, prepare_review_input,
)
from naiyue_test_agent.model_access import ModelCatalog, MODEL_LIMITS_METADATA_KEY
from naiyue_test_agent.settings import AppSettings
from test_dynamic_tools import DynamicModel


def verdict(decision='allow', **values):
    return AIMessage(content=json.dumps({'decision': decision, 'reason': 'bounded reason', **values}),
                     response_metadata={'finish_reason': 'stop'})


def model(responses, *, window=4096, output=128):
    return DynamicModel(responses=responses, metadata={MODEL_LIMITS_METADATA_KEY: {
        'context_window_tokens': window, 'max_output_tokens': output}})


def settings(**values):
    return AppSettings.model_validate({'permissions': {'mode': 'Auto', 'auto': {
        'fast_model_id': 'fast', 'detailed_model_id': 'detail', **values}}})


def reviewer(fast, detailed, **values):
    return AutoPermissionReviewer.from_catalog(ModelCatalog('fast', {'fast': fast, 'detail': detailed}), settings(**values).permissions.auto)


def prepared():
    return {'id': 'call', 'name': 'bash', 'args': {'command': 'python -m pytest'},
            'subject': {'operation': 'execute', 'args': {'command': 'python -m pytest'},
                        'side_effects': 'may_mutate', 'execution_environment': {'kind': 'host', 'platform': 'linux'}},
            'source': 'default'}


def payload():
    return prepare_review_input([HumanMessage(content='run the tests', id='u',
        additional_kwargs={'naiyue_human_input': True})], prepared(), 'PROJECT_RULE')


@async_test
@pytest.mark.parametrize('first,second,expected,calls', [('allow', 'ask', 'allow', 0),
    ('ask', 'allow', 'allow', 1), ('ask', 'ask', 'ask', 1)])
async def test_review_stage_contract_and_no_shared_model_mutation(first, second, expected, calls):
    fast, detailed = model([verdict(first)]), model([verdict(second)])
    original = dict(fast.metadata)
    review = reviewer(fast, detailed)
    result = (await review.areview(payload(), {'session_id': 's', 'run_id': 'r'}))
    assert result['decision'] == expected
    assert len(fast.seen) == 1 and len(detailed.seen) == calls
    assert isinstance(fast.seen[0][0], SystemMessage)
    assert 'permission reviewer' in fast.seen[0][0].content
    assert fast.bound == [[]] and fast.metadata == original
    if calls:
        assert fast.seen[0][-1].content == detailed.seen[0][-1].content
        assert fast.seen[0][0].content != detailed.seen[0][0].content


@async_test
@pytest.mark.parametrize('response', [verdict('deny'), verdict(extra='not allowed'),
    AIMessage(content='not json'), AIMessage(content='{"decision":"ask","decision":"allow","reason":"x"}'),
    verdict(reason='x' * 513), AIMessage(content='{"decision":"allow","reason":""}'),
    AIMessage(content='{"decision":"allow","reason":"ok"}', response_metadata={'finish_reason': 'length'}),
    AIMessage(content='{"decision":"allow","reason":"ok"}', tool_calls=[{'name': 'bash', 'args': {}, 'id': 'bad'}]),
])
async def test_invalid_fast_response_goes_to_human_without_detailed_call(response):
    fast, detailed = model([response]), model([verdict()])
    assert (await reviewer(fast, detailed).areview(payload(), {}))['decision'] == 'ask'
    assert len(fast.seen) == 1 and detailed.seen == []


@async_test
@pytest.mark.parametrize('stage', ['fast', 'detail'])
async def test_network_error_is_safe_ask_without_retry(stage, caplog):
    caplog.set_level(logging.INFO, logger='naiyue_test_agent.agent_runtime.tool_execution.permissions')
    class Failing(DynamicModel):
        async def _agenerate(self, *args, **kwargs):
            self.seen.append(True)
            raise TimeoutError('PRIVATE_PROVIDER_DETAIL')
    failing = Failing(responses=[], metadata=model([]).metadata)
    fast = failing if stage == 'fast' else model([verdict('ask')])
    detail = model([]) if stage == 'fast' else failing
    assert (await reviewer(fast, detail).areview(payload(), {'session_id': 's', 'run_id': 'r'}))['decision'] == 'ask'
    assert len(failing.seen) == 1
    assert detail.seen == [] if stage == 'fast' else len(detail.seen) == 1
    assert 'PRIVATE_PROVIDER_DETAIL' not in caplog.text
    entries = [record.permission_review for record in caplog.records if hasattr(record, 'permission_review')]
    assert entries[-1]['decision'] == 'ask' and entries[-1]['error_type'] == 'TimeoutError'


def test_projection_excludes_tool_results_assistant_prose_summary_and_unmarked_humans():
    current = prepared()
    messages = [HumanMessage(content='SUMMARY_PRIVATE', additional_kwargs={'naiyue_compaction_summary': True, 'naiyue_human_input': True}),
        HumanMessage(content='UNMARKED_PRIVATE'),
        HumanMessage(content='real task', id='user', additional_kwargs={'naiyue_human_input': True}),
        AIMessage(content='ASSISTANT_PRIVATE', tool_calls=[{'name': 'write', 'args': {'path': 'test.py', 'content': 'PAYLOAD_MUST_STAY'}, 'id': 'write'}]),
        ToolMessage(content='TOOL_PRIVATE', tool_call_id='write'),
        AIMessage(content='', tool_calls=[{'name': current['name'], 'args': current['args'], 'id': current['id']}])]
    result = prepare_review_input(messages, current, 'PROJECT_RULE')
    assert collect_human_messages(messages)[0]['content'] == 'real task'
    encoded = json.dumps(result)
    assert 'PRIVATE' not in encoded and 'PAYLOAD_MUST_STAY' in encoded
    assert result['project_instructions'] == 'PROJECT_RULE'
    assert result['current_call']['args'] == current['args']
    assert [c['name'] for c in result['previous_calls']] == ['write']


def test_only_text_parts_of_human_input_are_sent():
    messages = [HumanMessage(content=[{'type': 'text', 'text': 'real task'},
        {'type': 'image_url', 'image_url': {'url': 'PRIVATE_BASE64'}}],
        additional_kwargs={'naiyue_human_input': True})]
    assert collect_human_messages(messages)[0]['content'] == 'real task'


@async_test
@pytest.mark.parametrize('unavailable', ['no_user', 'current_too_large', 'project_too_large', 'missing_limits'])
async def test_missing_intent_or_budget_never_sends_partial_current_call(unavailable):
    fast, detailed = model([verdict()], window=2048), model([verdict()], window=2048)
    data = payload()
    if unavailable == 'no_user':
        data['human_messages'] = []
    elif unavailable == 'current_too_large':
        data['current_call']['args']['command'] = 'CURRENT_END ' * 10000
    elif unavailable == 'project_too_large':
        data['project_instructions'] = 'PROJECT_END ' * 10000
    else:
        fast.metadata = {}
    assert (await reviewer(fast, detailed).areview(data, {}))['decision'] == 'ask'
    assert fast.seen == detailed.seen == []


@async_test
async def test_old_history_can_be_removed_but_current_call_and_project_are_complete():
    fast, detailed = model([verdict()], window=2048), model([verdict()], window=2048)
    data = payload()
    data['previous_calls'] = [{'sequence': -1, 'id': 'old', 'name': 'write', 'args': {'content': 'OLD ' * 10000}}]
    result = (await reviewer(fast, detailed).areview(data, {}))
    assert result['decision'] == 'allow'
    sent = json.loads(fast.seen[0][-1].content)
    assert sent['previous_calls'] == []
    assert sent['current_call'] == data['current_call'] and sent['project_instructions'] == data['project_instructions']
    assert data['previous_calls']


@async_test
async def test_graph_control_is_not_an_ask():
    failure = GraphBubbleUp('control')
    class Signalling(DynamicModel):
        async def _agenerate(self, *args, **kwargs):
            raise failure
    review = reviewer(Signalling(responses=[], metadata=model([]).metadata), model([]))
    with pytest.raises(GraphBubbleUp) as caught:
        (await review.areview(payload(), {}))
    assert caught.value is failure


@async_test
async def test_reasoning_blocks_are_supported_and_only_safe_usage_is_logged(caplog):
    caplog.set_level(logging.INFO, logger='naiyue_test_agent.agent_runtime.tool_execution.permissions')
    response = AIMessage(content=[{'type': 'thinking', 'thinking': 'PRIVATE_THINKING'},
        {'type': 'text', 'text': '{"decision":"allow","reason":"PRIVATE_REASON"}'}],
        response_metadata={'stop_reason': 'end_turn'},
        usage_metadata={'input_tokens': 20, 'output_tokens': 4, 'total_tokens': 24})
    fast, detailed = model([response]), model([])
    assert (await reviewer(fast, detailed).areview(payload(), {'session_id': 's', 'run_id': 'r', '_review_call_id': 'c'}))['decision'] == 'allow'
    entries = [r.permission_review for r in caplog.records if hasattr(r, 'permission_review')]
    assert len(entries) == 1 and entries[0]['input_tokens'] == 20 and entries[0]['output_tokens'] == 4
    assert entries[0]['stage'] == 'fast' and entries[0]['tool_call_id'] == 'c'
    assert 'PRIVATE' not in caplog.text and 'PROJECT_RULE' not in caplog.text and 'pytest' not in caplog.text


@pytest.mark.parametrize('values', [{'fast_model_id': ''}, {'detailed_model_id': 3},
    {'max_reason_chars': True}, {'max_reason_chars': 0}, {'extra': 'no'}])
def test_invalid_auto_fields_fail_validation(values):
    with pytest.raises(ValueError):
        settings(**values)


def test_settings_loader_keeps_auto_configuration_and_tool_budgets(tmp_path):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[permissions]\nmode="Auto"\n[permissions.auto]\nfast_model_id="fast"\ndetailed_model_id="detail"\nmax_reason_chars=80\n[tools.read]\ndefault_limit=12\n')
    loaded = load_settings(path)
    assert loaded.permissions.mode == 'Auto' and loaded.permissions.auto.max_reason_chars == 80
    assert loaded.tools.read.default_limit == 12


@async_test
async def test_unmarked_stored_history_is_not_upgraded_to_human_intent():
    fast, detailed = model([verdict()]), model([])
    data = prepare_review_input([HumanMessage(content='old unmarked history')], prepared(), None)
    assert (await reviewer(fast, detailed).areview(data, {}))['decision'] == 'ask'
    assert fast.seen == detailed.seen == []


@async_test
async def test_diagnostic_failure_does_not_change_review_decision(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime.tool_execution import permissions
    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE_DIAGNOSTIC')
    monkeypatch.setattr(permissions.logger, 'info', fail)
    assert (await reviewer(model([verdict()]), model([])).areview(payload(), {}))['decision'] == 'allow'
    assert 'auto_review_diagnostics_failed' in caplog.text and 'PRIVATE_DIAGNOSTIC' not in caplog.text
