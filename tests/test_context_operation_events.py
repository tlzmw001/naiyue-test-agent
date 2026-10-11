"""Context operations end at confirmed replacement, not summary return."""

import logging

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.agent_runtime.context_compaction import ContextCompactionError
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
from test_context_compaction import _app, _model
from test_context_overflow_recovery import _configured_model, _history, _status


OP_TYPES = frozenset({'compaction_started', 'compaction_finished',
                      'context_recovery_started', 'context_recovery_finished'})


def operation_events(events):
    return [e for e in events if e.event_type in OP_TYPES]


def prepared_app(monkeypatch, summary=None):
    a = _model([AIMessage(content='one'), AIMessage(content='two')], window=10000)
    b = _model([AIMessage(content='final')], window=300, output=10, reserve=250, keep=10,
               summaries=[summary if summary is not None else AIMessage(content='PRIVATE_SUMMARY', usage_metadata={
                   'input_tokens': 20, 'output_tokens': 5, 'total_tokens': 25,
               })], model_id='B')
    app = _app(monkeypatch, {'A': a, 'B': b})
    session = app.create_session('A')
    app.run(session, [('user', 'PRIVATE_FIRST_' + 'x' * 100)])
    app.run(session, [('user', 'PRIVATE_SECOND_' + 'y' * 100)])
    app.select_model(session, 'B')
    events = []
    app.subscribe_events(events.append)
    return app, session, b, events


def test_threshold_compaction_finishes_only_after_expected_summary_is_visible(monkeypatch, caplog):
    app, session, model, events = prepared_app(monkeypatch)
    with caplog.at_level(logging.INFO):
        result = app.run(session, [('user', 'third')])
    operations = operation_events(events)
    assert [e.event_type for e in operations] == ['compaction_started', 'compaction_finished']
    start, end = operations
    assert start.data.operation_id == end.data.operation_id
    assert start.data.model_id == 'B' and start.data.trigger == 'threshold'
    assert end.data.status == 'applied' and end.data.visibility == 'graph_values'
    assert end.data.summary_message_id == result['messages'][0].id
    summary_event = next(e for e in events if e.event_type == 'message_completed' and e.data.is_context_summary)
    assert summary_event.sequence < end.sequence
    assert end.data.summary_input_tokens == 20 and end.data.summary_output_tokens == 5
    assert end.data.summary_duration_ms >= 0 and end.data.after_estimate < start.data.before_estimate
    assert len(model.summary_inputs) == len(model.normal_inputs) == 1
    assert 'PRIVATE_' not in repr(operations) + caplog.text


def test_below_threshold_does_not_invent_compaction_operations(monkeypatch):
    model = _model([AIMessage(content='done')], window=10000)
    app = _app(monkeypatch, {'A': model})
    events = []
    app.subscribe_events(events.append)
    app.run(app.create_session('A'), [('user', 'short')])
    assert operation_events(events) == [] and model.summary_inputs == []


def test_eligible_summary_failure_falls_back_and_normal_request_still_runs(monkeypatch):
    app, session, model, events = prepared_app(monkeypatch, TimeoutError('PRIVATE_TIMEOUT'))
    result = app.run(session, [('user', 'third')])
    start, end = operation_events(events)
    assert start.event_type == 'compaction_started'
    assert end.data.status == 'fallback' and end.data.error_type == 'TimeoutError'
    assert end.data.summary_message_id is None and end.data.visibility is None
    assert result['messages'][0].content.startswith('PRIVATE_FIRST_')
    assert len(model.summary_inputs) == len(model.normal_inputs) == 1
    assert events[-1].data.status == 'completed'


def test_invalid_summary_fails_without_normal_model_call_or_applied_event(monkeypatch):
    app, session, model, events = prepared_app(monkeypatch, AIMessage(content='bad', tool_calls=[
        {'name': 'unexpected', 'args': {}, 'id': 'bad'},
    ]))
    with pytest.raises(ContextCompactionError) as caught:
        app.run(session, [('user', 'third')])
    assert str(caught.value) == 'summary_invalid'
    end = operation_events(events)[-1].data
    assert end.status == 'failed' and end.reason == 'summary_invalid'
    assert model.normal_inputs == [] and events[-1].data.status == 'failed'


def test_explicit_overflow_recovery_waits_for_gate_and_state_and_is_one_model_turn(monkeypatch):
    model = _configured_model([_status(), AIMessage(content='recovered')])
    app = _app(monkeypatch, {'A': model})
    events = []
    app.subscribe_events(events.append)
    result = app.run(app.create_session('A'), _history())
    operations = operation_events(events)
    assert [e.event_type for e in operations] == [
        'context_recovery_started', 'compaction_started', 'compaction_finished', 'context_recovery_finished',
    ]
    recovery_start, compaction_start, compaction_end, recovery_end = operations
    assert recovery_end.data.operation_id == recovery_start.data.operation_id
    assert recovery_end.data.related_operation_id == compaction_start.data.operation_id
    assert compaction_start.data.trigger == 'overflow' and compaction_end.data.status == 'applied'
    assert recovery_end.data.status == 'recovered' and recovery_end.data.retry_attempts == 1
    assert recovery_end.data.model_call_id == next(e.data.model_call_id for e in events if e.event_type == 'model_call_started')
    assert result['messages'][0].id == compaction_end.data.summary_message_id
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1
    assert sum(e.event_type == 'model_call_started' for e in events) == 1
    assert sum(e.event_type == 'turn_started' for e in events) == 1


def test_retry_response_rejected_does_not_report_recovery_or_replacement_success(monkeypatch):
    model = _configured_model([_status(), AIMessage(content='PRIVATE_REJECTED', invalid_tool_calls=[
        {'name': 'probe', 'args': '{', 'id': 'bad', 'error': 'parse'},
    ])])
    app = _app(monkeypatch, {'A': model})
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(ModelResponseRejected) as caught:
        app.run(app.create_session('A'), _history())
    assert caught.value.reason == 'invalid_tool_calls'
    operations = operation_events(events)
    compaction = next(e.data for e in operations if e.event_type == 'compaction_finished')
    recovery = next(e.data for e in operations if e.event_type == 'context_recovery_finished')
    assert compaction.status == 'unconfirmed' and compaction.summary_message_id is None
    assert recovery.status == 'failed' and recovery.stage == 'response_validation'
    assert 'PRIVATE_' not in repr(operations)
