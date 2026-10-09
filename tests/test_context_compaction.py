"""Completed-turn compaction uses the current model and preserves checkpoint facts."""

import json
import logging
from xml.etree import ElementTree

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.context_compaction import (
    ContextCompactionError, _serialize_text_history, _validate_removed_prefix,
)
from naiyue_test_agent.model_access import ModelCatalog


class RoutedFake(FakeMessagesListChatModel):
    summary_responses: list[object]
    summary_inputs: list = []
    summary_options: list = []
    normal_inputs: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        if messages and str(messages[0].content).startswith('Summarize completed'):
            self.summary_inputs.append(messages)
            self.summary_options.append(kwargs)
            response = self.summary_responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return ChatResult(generations=[ChatGeneration(message=response)])
        self.normal_inputs.append(messages)
        return super()._generate(messages, *args, **kwargs)


def _model(responses, *, window, output=20, reserve=20, keep=10,
           summary_output=8, summaries=None, model_id='A'):
    return RoutedFake(
        responses=responses, summary_responses=list(summaries or []),
        metadata={
            'naiyue_model_limits': {
                'model_id': model_id, 'context_window_tokens': window,
                'max_output_tokens': output, 'context_window_source': 'config',
                'max_output_source': 'config',
            },
            'naiyue_compaction_reserve': {'tokens': reserve, 'source': 'config'},
            'naiyue_compaction_keep_recent': {'tokens': keep, 'source': 'config'},
            'naiyue_compaction_summary_output': {
                'tokens': summary_output, 'source': 'config',
            },
        },
    )


def _app(monkeypatch, models, *, tools=None):
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', models))
    return application.build_local_application(
        'synthetic.json', tools=tools or [], system_prompt='BASE',
        response_format=None,
    )


def test_complete_turn_compaction_switches_model_and_keeps_old_checkpoint(
    monkeypatch, caplog,
):
    a = _model([AIMessage(content='A-one'), AIMessage(content='A-two')],
               window=10000, model_id='A')
    b = _model([AIMessage(content='B-three'), AIMessage(content='B-four')],
               window=300, output=10, reserve=250, keep=10,
               summaries=[AIMessage(content='first-summary'),
                          AIMessage(content='updated-summary')], model_id='B')
    app = _app(monkeypatch, {'A': a, 'B': b})
    session = app.create_session('A')
    app.run(session, [('user', 'FIRST_FACT_' + 'x' * 100)])
    second = app.run(session, [('user', 'SECOND_FACT_' + 'y' * 100)])
    app.select_model(session, 'B')
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        third = app.run(session, [('user', 'third')])
    assert len(b.summary_inputs) == 1
    assert b.summary_options[0]['max_tokens'] == 8
    assert 'FIRST_FACT_' in b.summary_inputs[0][-1].content
    assert [m.type for m in third['messages']] == ['human', 'human', 'ai',
                                                    'human', 'ai']
    assert third['messages'][0].additional_kwargs['naiyue_compaction_summary'] is True
    assert 'first-summary' in third['messages'][0].content
    assert third['messages'][1].content.startswith('SECOND_FACT_')
    assert [m.content for m in second['messages'][:2]] == [
        'FIRST_FACT_' + 'x' * 100, 'A-one',
    ]
    thread_id = app._sessions._sessions[session]['thread_id']
    history = list(app._agent.get_state_history({'configurable': {'thread_id': thread_id}}))
    assert any(snapshot.values.get('messages') and
               snapshot.values['messages'][0].content.startswith('FIRST_FACT_')
               for snapshot in history)
    events = [r.context_compaction for r in caplog.records
              if hasattr(r, 'context_compaction')]
    assert any(event['event'] == 'context_summary_model_started'
               and event['model_id'] == 'B'
               and event['summary_output_cap_tokens'] == 8
               for event in events)
    assert any(event['event'] == 'context_summary_model_finished'
               and event['outcome'] == 'returned'
               and event['duration_ms'] >= 0
               for event in events)
    assert any(event['event'] == 'context_compaction_completed' and
               event['model_id'] == 'B' and
               50 < event['estimated_post_compaction_input_tokens'] <= 290
               for event in events)
    assert 'FIRST_FACT_' not in caplog.text

    fourth = app.run(session, [('user', 'fourth')])
    assert len(b.summary_inputs) == 2
    assert '<previous-summary>' in b.summary_inputs[1][-1].content
    assert 'first-summary' in b.summary_inputs[1][-1].content
    assert 'updated-summary' in fourth['messages'][0].content


def test_compaction_never_splits_tool_call_from_result(monkeypatch):
    @tool
    def probe() -> str:
        """Return a short tool value."""
        return 'TOOL_RESULT'

    a = _model([
        AIMessage(content='', tool_calls=[{'name': 'probe', 'args': {}, 'id': 'call-1'}]),
        AIMessage(content='A-done'), AIMessage(content='A-next'),
    ], window=10000, model_id='A')
    b = _model([AIMessage(content='B-done')], window=400, output=10,
               reserve=340,
               summaries=[AIMessage(content='summary-with-tool')], model_id='B')
    app = _app(monkeypatch, {'A': a, 'B': b}, tools=[probe])
    session = app.create_session('A')
    app.run(session, [('user', 'FIRST_TOOL_TURN_' + 'x' * 110)])
    app.run(session, [('user', 'SECOND_TURN_' + 'y' * 100)])
    app.select_model(session, 'B')
    result = app.run(session, [('user', 'third')])
    assert len(b.summary_inputs) == 1
    prompt = b.summary_inputs[0][-1].content
    assert 'call-1' in prompt and 'TOOL_RESULT' in prompt
    assert 'tool_call_id="call-1"' in prompt
    assert not any(isinstance(m, ToolMessage) for m in result['messages'][1:])
    assert result['messages'][1].content.startswith('SECOND_TURN_')


@pytest.mark.parametrize(('summary_result', 'reason'), [
    (RuntimeError('SECRET_PROVIDER_FAILURE'), 'summary_failed'),
    (AIMessage(content=''), 'summary_invalid'),
    (AIMessage(content='partial', response_metadata={'finish_reason': 'length'}),
     'summary_invalid'),
])
def test_summary_failure_keeps_original_messages_and_releases_run(
    monkeypatch, caplog, summary_result, reason,
):
    a = _model([AIMessage(content='A-one'), AIMessage(content='A-two')],
               window=10000, model_id='A')
    b = _model([AIMessage(content='unused')], window=300, output=220,
               reserve=250,
               summaries=[summary_result], model_id='B')
    app = _app(monkeypatch, {'A': a, 'B': b})
    session = app.create_session('A')
    app.run(session, [('user', 'FIRST_FACT_' + 'x' * 100)])
    previous = app.run(session, [('user', 'SECOND_FACT_' + 'y' * 100)])
    app.select_model(session, 'B')
    assert count_tokens_approximately([
        SystemMessage(content='BASE'), *previous['messages'], HumanMessage(content='third'),
    ]) > 300 - 220
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(ContextCompactionError, match=reason):
            app.run(session, [('user', 'third')])
    assert b.normal_inputs == []
    assert app._sessions._sessions[session]['busy'] is False
    thread_id = app._sessions._sessions[session]['thread_id']
    latest = app._agent.get_state({'configurable': {'thread_id': thread_id}})
    assert latest.values['messages'][0].content.startswith('FIRST_FACT_')
    assert latest.values['messages'][-1].content == 'third'
    assert not any(m.additional_kwargs.get('naiyue_compaction_summary')
                   for m in latest.values['messages'])
    assert 'SECRET_PROVIDER_FAILURE' not in caplog.text


def test_summary_input_over_budget_fails_before_summary_model(monkeypatch):
    a = _model([AIMessage(content='A-one'), AIMessage(content='A-two')],
               window=10000, model_id='A')
    b = _model([AIMessage(content='unused')], window=100, output=20,
               summaries=[AIMessage(content='unused')], model_id='B')
    app = _app(monkeypatch, {'A': a, 'B': b})
    session = app.create_session('A')
    app.run(session, [('user', 'FIRST_FACT_' + 'x' * 1000)])
    app.run(session, [('user', 'SECOND_FACT_' + 'y' * 100)])
    app.select_model(session, 'B')
    with pytest.raises(ContextCompactionError, match='summary_input_exceeds_budget'):
        app.run(session, [('user', 'third')])
    assert b.summary_inputs == []
    assert b.normal_inputs == []


def test_summary_that_cannot_fit_preserved_turn_never_calls_normal_model(
    monkeypatch, caplog,
):
    a = _model([AIMessage(content='A-one'), AIMessage(content='A-two')],
               window=10000, model_id='A')
    b = _model([AIMessage(content='must-not-be-called')], window=500,
               output=20, reserve=50, keep=30,
               summaries=[AIMessage(content='short summary')], model_id='B')
    app = _app(monkeypatch, {'A': a, 'B': b})
    session = app.create_session('A')
    app.run(session, [('user', 'OLD_FACT_' + 'x' * 320)])
    app.run(session, [('user', 'LARGE_RECENT_TURN_' + 'y' * 3300)])
    app.select_model(session, 'B')

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        with pytest.raises(ContextCompactionError,
                           match='compacted_request_over_budget'):
            app.run(session, [('user', 'new question')])

    assert len(b.summary_inputs) == 1
    assert b.normal_inputs == []
    assert app._sessions._sessions[session]['busy'] is False
    thread_id = app._sessions._sessions[session]['thread_id']
    latest = app._agent.get_state({'configurable': {'thread_id': thread_id}})
    assert latest.values['messages'][0].content.startswith('OLD_FACT_')
    assert latest.values['messages'][-1].content == 'new question'
    assert not any(message.additional_kwargs.get('naiyue_compaction_summary')
                   for message in latest.values['messages'])
    events = [record.context_compaction for record in caplog.records
              if hasattr(record, 'context_compaction')]
    assert any(event['event'] == 'context_compaction_failed'
               and event['reason'] == 'compacted_request_over_budget'
               for event in events)
    assert not any(event['event'] == 'context_compaction_completed'
                   for event in events)


def test_restored_invalid_tool_call_and_later_correction_can_be_summarized(
    monkeypatch,
):
    model = _model([AIMessage(content='continued')], window=500,
                   output=20, reserve=430, keep=10,
                   summaries=[AIMessage(content='old request was corrected')])
    app = _app(monkeypatch, {'A': model})
    session = app.create_session('A')
    result = app.run(session, [
        HumanMessage(content='OLD_REQUEST_' + 'x' * 280),
        AIMessage(content='', invalid_tool_calls=[
            {'id': 'bad-1', 'name': 'read', 'args': '{"path":"<&',
             'error': 'invalid JSON <&>'},
            {'id': None, 'name': None, 'args': None, 'error': None},
        ]),
        HumanMessage(content='Please correct the malformed read call.'),
        AIMessage(content='I will use a valid path next time.'),
        HumanMessage(content='RECENT_' + 'y' * 120),
    ])

    assert len(model.summary_inputs) == 1
    prompt = model.summary_inputs[0][-1].content
    history = prompt.split('<conversation>\n', 1)[1].split('\n</conversation>', 1)[0]
    root = ElementTree.fromstring(f'<conversation>{history}</conversation>')
    invalid = root.findall('.//invalid_tool_call')
    assert [item.attrib for item in invalid] == [
        {'parse_status': 'invalid'}, {'parse_status': 'invalid'},
    ]
    assert json.loads(invalid[0].text) == {
        'id': 'bad-1', 'name': 'read', 'args': '{"path":"<&',
        'error': 'invalid JSON <&>',
    }
    assert json.loads(invalid[1].text) == {
        'id': None, 'name': None, 'args': None, 'error': None,
    }
    assert root.findall('.//tool_call') == []
    assert root.findall('.//message[@type="tool"]') == []
    assert 'Please correct the malformed read call.' in prompt
    assert 'I will use a valid path next time.' in prompt
    assert 'invalid tool calls' in model.summary_inputs[0][0].content.lower()
    assert len(model.normal_inputs) == 1
    assert result['messages'][0].additional_kwargs['naiyue_compaction_summary']


def test_mixed_restored_calls_keep_valid_error_result_distinct_from_invalid_call():
    messages = [
        HumanMessage(content='old'),
        AIMessage(content='',
                  tool_calls=[{'id': 'valid-1', 'name': 'read', 'args': {'path': 'a'}}],
                  invalid_tool_calls=[{'id': 'bad-1', 'name': 'edit',
                                       'args': '{bad', 'error': 'invalid JSON'}]),
        ToolMessage(content='Permission denied', tool_call_id='valid-1',
                    status='error'),
    ]
    _validate_removed_prefix(messages)
    root = ElementTree.fromstring(
        f'<conversation>{_serialize_text_history(messages)}</conversation>')
    assert root.find('.//tool_call').attrib['id'] == 'valid-1'
    assert root.find('.//invalid_tool_call').attrib['parse_status'] == 'invalid'
    assert root.find('.//message[@type="tool"]').attrib == {
        'type': 'tool', 'tool_call_id': 'valid-1', 'status': 'error',
    }


def test_same_ai_message_cannot_pair_valid_and_invalid_calls_with_same_id():
    messages = [
        AIMessage(content='',
                  tool_calls=[{'id': 'same-1', 'name': 'read', 'args': {}}],
                  invalid_tool_calls=[{'id': 'same-1', 'name': 'edit',
                                       'args': '{bad', 'error': 'invalid JSON'}]),
        ToolMessage(content='ok', tool_call_id='same-1'),
    ]
    with pytest.raises(ContextCompactionError, match='ambiguous_tool_call_id'):
        _validate_removed_prefix(messages)


def test_unsupported_multimodal_old_history_is_not_silently_summarized(monkeypatch):
    a = _model([AIMessage(content='A-one'), AIMessage(content='A-two')],
               window=10000, model_id='A')
    b = _model([AIMessage(content='unused')], window=300, output=10,
               reserve=250, model_id='B')
    app = _app(monkeypatch, {'A': a, 'B': b})
    session = app.create_session('A')
    app.run(session, [HumanMessage(content=[
        {'type': 'image_url', 'image_url': {'url': 'https://example.test/image.png'}},
    ])])
    app.run(session, [('user', 'SECOND_FACT_' + 'y' * 100)])
    app.select_model(session, 'B')
    with pytest.raises(ContextCompactionError, match='unsupported_summary_content'):
        app.run(session, [('user', 'third')])
    assert b.summary_inputs == []
    assert b.normal_inputs == []


def test_no_complete_old_turn_fails_without_replacing_messages(monkeypatch):
    b = _model([AIMessage(content='B-one')], window=100, output=10,
               keep=1000, model_id='A')
    app = _app(monkeypatch, {'A': b})
    session = app.create_session('A')
    with pytest.raises(ContextCompactionError, match='no_safe_cut'):
        app.run(session, [('user', 'ONLY_TURN_' + 'x' * 400)])
    assert b.summary_inputs == []


@pytest.mark.parametrize(('result_chars', 'fits_send_budget'), [
    (700, True), (800, False),
])
def test_soft_threshold_skips_or_splits_at_a_safe_message_boundary(
    monkeypatch, caplog, result_chars, fits_send_budget,
):
    model = _model([AIMessage(content='continued')], window=350,
                   output=20, reserve=60, keep=30, model_id='A',
                   summaries=[AIMessage(content='earlier request summary')])
    app = _app(monkeypatch, {'A': model})
    session = app.create_session('A')
    messages = [
        HumanMessage(content='check ' + 'u' * 300),
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': 'a.txt'}, 'id': 'call-1'},
        ]),
        ToolMessage(content='result ' + 'r' * result_chars,
                    tool_call_id='call-1'),
        AIMessage(content='I analyzed the result.'),
        HumanMessage(content='What next?'),
    ]
    estimated = count_tokens_approximately([SystemMessage(content='BASE'),
                                            *messages])
    assert estimated > 290
    assert (estimated <= 330) is fits_send_budget

    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        if fits_send_budget:
            result = app.run(session, messages)
            assert result['messages'][-1].content == 'continued'
            assert len(model.normal_inputs) == 1
        else:
            result = app.run(session, messages)
            assert result['messages'][-1].content == 'continued'
            assert len(model.normal_inputs) == 1
            assert model.normal_inputs[0][2].tool_calls[0]['id'] == 'call-1'
            assert model.normal_inputs[0][3].tool_call_id == 'call-1'
    assert len(model.summary_inputs) == (0 if fits_send_budget else 1)
    events = [record.context_compaction for record in caplog.records
              if hasattr(record, 'context_compaction')]
    assert any(event['event'] == 'context_compaction_skipped'
               and event['reason'] == 'no_safe_cut_within_send_budget'
               for event in events) is fits_send_budget
    assert app._sessions._sessions[session]['busy'] is False


def test_split_after_tool_result_summarizes_earlier_history_and_turn_prefix(
    monkeypatch, caplog,
):
    model = _model([AIMessage(content='continued')], window=530,
                   output=20, reserve=50, keep=30,
                   summaries=[AIMessage(content='brief split summary')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        'synthetic.json', tools=[], system_prompt='S' * 1000,
        response_format=None,
    )
    session = app.create_session('A')
    messages = [
        HumanMessage(content='earlier request'), AIMessage(content='earlier answer'),
        HumanMessage(content='check ' + 'u' * 300),
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': 'a.txt'}, 'id': 'call-1'},
        ]),
        ToolMessage(content='result ' + 'r' * 800, tool_call_id='call-1'),
        AIMessage(content='I analyzed the result.'),
        HumanMessage(content='What next?'),
    ]
    with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
        result = app.run(session, messages)

    assert len(model.summary_inputs) == 1
    prompt = model.summary_inputs[0][-1].content
    assert '<conversation>' in prompt and 'earlier request' in prompt
    assert '<turn-prefix>' in prompt and 'check ' in prompt
    assert 'call-1' in prompt and 'result ' in prompt
    assert len(model.normal_inputs) == 1
    normal = model.normal_inputs[0]
    assert [item.type for item in normal[1:]] == ['human', 'ai', 'human']
    assert normal[1].content.endswith('brief split summary')
    assert normal[2].content == 'I analyzed the result.'
    assert normal[3].content == 'What next?'
    assert [item.type for item in result['messages']] == ['human', 'ai',
                                                           'human', 'ai']
    events = [record.context_compaction for record in caplog.records
              if hasattr(record, 'context_compaction')]
    assert any(event['event'] == 'context_compaction_completed'
               and event['split_turn'] is True
               and event['cutoff_message_index'] == 5 for event in events)


def test_split_before_assistant_tool_call_keeps_its_result_pair(monkeypatch):
    model = _model([AIMessage(content='continued')], window=500,
                   output=20, reserve=50, keep=30,
                   summaries=[AIMessage(content='request and early work')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        'synthetic.json', tools=[], system_prompt='S' * 800,
        response_format=None,
    )
    session = app.create_session('A')
    result = app.run(session, [
        HumanMessage(content='check ' + 'u' * 100),
        AIMessage(content='early analysis ' + 'a' * 1000),
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': 'a.txt'}, 'id': 'call-2'},
        ]),
        ToolMessage(content='read succeeded', tool_call_id='call-2'),
        HumanMessage(content='What next?'),
    ])

    assert len(model.summary_inputs) == 1
    assert '<turn-prefix>' in model.summary_inputs[0][-1].content
    assert 'call-2' not in model.summary_inputs[0][-1].content
    normal = model.normal_inputs[0]
    assert [item.type for item in normal[1:]] == ['human', 'ai', 'tool', 'human']
    assert normal[2].tool_calls[0]['id'] == 'call-2'
    assert normal[3].tool_call_id == 'call-2'
    assert [item.type for item in result['messages']] == ['human', 'ai',
                                                           'tool', 'human', 'ai']
