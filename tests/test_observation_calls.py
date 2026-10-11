"""Explicit observation calls and normal-path health are separate contracts."""

import asyncio

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from naiyue_test_agent.agent_runtime.observation import observe
from naiyue_test_agent.agent_runtime.turn_observation import TurnObserver
from naiyue_test_agent.agent_runtime.tools import create_session_read_tool
from event_sequence import EventSequenceChecker
from test_application_run_events import ToolModel, make_app
from test_async_application import StreamingToolModel, make_app as make_async_app
from test_context_operation_events import prepared_app
from test_context_overflow_recovery import _configured_model, _history, _status
from test_context_compaction import _app


class ProbeObserver:
    def __init__(self):
        self.calls = []

    def begin(self, call_id, *, visibility):
        self.calls.append((call_id, visibility))
        return 'observed'


def test_bound_method_receives_arguments_and_returns_observation(caplog):
    observer = ProbeObserver()
    assert observe(observer, observer.begin, 'call', visibility='graph_values') == 'observed'
    assert observer.calls == [('call', 'graph_values')]
    assert not getattr(observer, '_observation_failed', False)
    assert caplog.records == []


def test_string_operation_is_rejected_before_optional_boundary():
    observer = ProbeObserver()
    with pytest.raises(TypeError, match='callable'):
        observe(observer, 'begin', 'call', visibility='graph_values')
    assert observer.calls == []
    assert not getattr(observer, '_observation_failed', False)


def test_missing_method_is_visible_before_helper_and_does_not_disable_observer(caplog):
    observer = ProbeObserver()
    with pytest.raises(AttributeError):
        observe(observer, observer.begni, 'call', visibility='graph_values')
    assert not getattr(observer, '_observation_failed', False)
    assert caplog.records == []


@pytest.mark.parametrize('error_type', [AttributeError, TypeError, ValueError])
def test_method_body_fault_is_isolated_once_with_safe_diagnostic(error_type, caplog):
    observer = ProbeObserver()

    def fail():
        observer.calls.append('entered')
        raise error_type('PRIVATE_OBSERVER_BODY')

    assert observe(observer, fail, fallback='unavailable') == 'unavailable'
    assert observe(observer, fail, fallback='unavailable') == 'unavailable'
    assert observer.calls == ['entered'] and observer._observation_failed
    assert len(caplog.records) == 1
    assert 'operation=fail' in caplog.text and f'error_type={error_type.__name__}' in caplog.text
    assert 'PRIVATE_OBSERVER_BODY' not in caplog.text


@pytest.mark.parametrize('error', [asyncio.CancelledError(), KeyboardInterrupt()])
def test_callable_base_exception_is_not_observation_degradation(error, caplog):
    observer = ProbeObserver()

    def interrupt():
        raise error

    with pytest.raises(type(error)) as caught:
        observe(observer, interrupt)
    assert caught.value is error
    assert not getattr(observer, '_observation_failed', False)
    assert caplog.records == []


def test_missing_or_disabled_observer_preserves_fallback_without_calling():
    observer = ProbeObserver()
    assert observe(None, observer.begin, 'call', visibility='graph_values', fallback='missing') == 'missing'
    observer._observation_failed = True
    assert observe(observer, observer.begin, 'call', visibility='graph_values', fallback='disabled') == 'disabled'
    assert observer.calls == []


def test_health_check_rejects_success_with_isolated_observer_fault(monkeypatch, caplog):
    app = make_app(monkeypatch)
    events = []
    app.subscribe_events(events.append)

    def fail(*_args):
        raise TypeError('PRIVATE_BAD_OBSERVATION')

    monkeypatch.setattr(TurnObserver, 'begin', fail)
    assert app.run(app.create_session('A'), [('user', 'probe')])['messages'][-1].content == 'PRIVATE_REPLY'
    with pytest.raises(AssertionError, match='Unexpected observation diagnostics'):
        EventSequenceChecker.assert_healthy(events, caplog.records)


def test_health_check_rejects_unavailable_observation_without_exception(monkeypatch, caplog):
    app = make_app(monkeypatch)
    events = []
    app.subscribe_events(events.append)
    monkeypatch.setattr(TurnObserver, 'begin', lambda self, _call: self.disable())
    assert app.run(app.create_session('A'), [('user', 'probe')])['messages'][-1].content == 'PRIVATE_REPLY'
    with pytest.raises(AssertionError, match='Unexpected observation unavailability'):
        EventSequenceChecker.assert_healthy(events, caplog.records)


def test_sync_tool_cycle_has_healthy_observation(monkeypatch, caplog):
    @tool
    def probe() -> str:
        """Return a deterministic tool result."""
        return 'tool result'

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'probe-call'}],
        response_metadata={'finish_reason': 'tool_calls'}),
        AIMessage(content='done', response_metadata={'finish_reason': 'stop'})])
    app = make_app(monkeypatch, model, [probe])
    events = []
    app.subscribe_events(events.append)
    assert app.run(app.create_session('A'), [('user', 'run')])['messages'][-1].content == 'done'
    EventSequenceChecker.assert_healthy(events, caplog.records)
    assert sum(event.event_type == 'turn_finished' for event in events) == 2
    assert sum(event.event_type == 'tool_call_finished' for event in events) == 1


def test_async_steering_follow_up_have_healthy_observation(monkeypatch, caplog):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content=text) for text in ('one', 'two', 'three')],
                                   entered=entered, release=release)
        app = await make_async_app(monkeypatch, model)
        events = []
        app.subscribe_events(events.append)
        session = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(session, [('user', 'start')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            app.steer(session, 'steering')
            app.follow_up(session, 'follow-up')
            release.set()
            result = await asyncio.wait_for(task, 5)
            assert [message.content for message in result['messages']] == [
                'start', 'one', 'steering', 'two', 'follow-up', 'three']
            EventSequenceChecker.assert_healthy(events, caplog.records)
            assert sum(event.event_type == 'control_delivered' for event in events) == 2
            assert any(event.event_type == 'message_delta' for event in events)
        finally:
            release.set()
            await app.aclose()
    asyncio.run(scenario())


def test_async_stop_has_healthy_observation(monkeypatch, caplog):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[], entered=entered, release=release)
        app = await make_async_app(monkeypatch, model)
        events = []
        app.subscribe_events(events.append)
        session = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(session, [('user', 'start')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            app.request_stop(session)
            result = await asyncio.wait_for(task, 5)
            assert result['run_stopped'] is True
            EventSequenceChecker.assert_healthy(events, caplog.records)
            assert events[-1].data.status == 'stopped'
            assert any(event.event_type == 'control_applied' for event in events)
        finally:
            release.set()
            await app.aclose()
    asyncio.run(scenario())


def test_approval_and_resume_have_healthy_observation(monkeypatch, tmp_path, caplog):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('approved', encoding='utf-8')
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read-call'}]), AIMessage(content='done')])
    app = make_app(monkeypatch, model, [create_session_read_tool()])
    session = app.create_workspace_session('A', root)
    events = []
    app.subscribe_events(events.append)
    paused = app.run(session, [('user', 'read')])
    assert paused['approval_requests'] and events[-1].data.status == 'interrupted'
    EventSequenceChecker.assert_healthy(events, caplog.records)
    events.clear()
    result = app.resume_tool_approval(session, ['approve'])
    assert result['messages'][-1].content == 'done'
    EventSequenceChecker.assert_healthy(events, caplog.records)
    assert any(event.event_type == 'turn_resumed' for event in events)
    assert any(event.event_type == 'control_applied' for event in events)


@pytest.mark.parametrize('operation', ['compaction', 'recovery'])
def test_context_operations_have_healthy_observation(monkeypatch, caplog, operation):
    if operation == 'compaction':
        app, session, _model, events = prepared_app(monkeypatch)
        messages = [('user', 'third')]
    else:
        model = _configured_model([_status(), AIMessage(content='recovered')])
        app = _app(monkeypatch, {'A': model})
        session, events = app.create_session('A'), []
        app.subscribe_events(events.append)
        messages = _history()
    app.run(session, messages)
    EventSequenceChecker.assert_healthy(events, caplog.records)
    expected = 'compaction_finished' if operation == 'compaction' else 'context_recovery_finished'
    assert any(event.event_type == expected for event in events)
