"""Logical model and tool calls share application run metadata notifications."""

from naiyue_test_agent.agent_runtime.async_invocation import ainvoke_agent_run

from native_application_helpers import async_test

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.application import prepare_tool_registry

from dataclasses import FrozenInstanceError

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime import build_runtime_agent
from naiyue_test_agent.agent_runtime.events import RunStartedData, RunFinishedData
from naiyue_test_agent.application.run_events import ApplicationEventSubscriptions
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
from test_application_run_events import ToolModel, make_app


def events_of(events, name):
    return [e for e in events if e.event_type == name]


CALL_METADATA_EVENTS = frozenset({
    'run_started', 'run_finished', 'model_call_started', 'model_call_finished',
    'tool_call_started', 'tool_call_finished',
})


def call_metadata_only(handler):
    """Spec 063 protects call metadata; Spec 064 separately protects content."""
    def observe(event):
        if event.event_type in CALL_METADATA_EVENTS:
            return handler(event)
    return observe


@async_test
async def test_one_tool_loop_has_correlated_immutable_safe_events(monkeypatch, caplog):
    @tool
    def probe(value: str) -> str:
        """Read one controlled value."""
        assert value == 'PRIVATE_ARGUMENT'
        return 'PRIVATE_OUTPUT'

    model = ToolModel(responses=[
        AIMessage(content='PRIVATE_MODEL', tool_calls=[
            {'name': 'probe', 'args': {'value': 'PRIVATE_ARGUMENT'}, 'id': 'probe-1'},
        ], response_metadata={'finish_reason': 'tool_calls'}, usage_metadata={
            'input_tokens': 11, 'output_tokens': 3, 'total_tokens': 14,
            'input_token_details': {'cache_read': 6},
        }),
        AIMessage(content='PRIVATE_FINAL', response_metadata={'finish_reason': 'stop'}),
    ])
    app = await make_app(monkeypatch, model, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(call_metadata_only(events.append))
    session = await app.acreate_session('A')
    with caplog.at_level('INFO'):
        result = await app.arun(session, [('user', 'PRIVATE_PROMPT')])
    assert [e.event_type for e in events] == [
        'run_started', 'model_call_started', 'model_call_finished',
        'tool_call_started', 'tool_call_finished',
        'model_call_started', 'model_call_finished', 'run_finished',
    ]
    # Native streams report one message_delta for each text reply;
    # Registered dispatches report queued/preparing; each model input also emits
    # its prepared and returned context statistics, outside this metadata filter.
    assert [e.sequence for e in events] == [1, 4, 8, 10, 13, 17, 21, 24]
    assert all(e.session_id == session and e.run_id == events[0].run_id for e in events)
    starts = events_of(events, 'model_call_started')
    ends = events_of(events, 'model_call_finished')
    assert [e.data.model_call_id for e in starts] == [e.data.model_call_id for e in ends]
    assert len({e.data.model_call_id for e in starts}) == 2
    assert all(e.data.model_id == 'A' and e.data.purpose == 'agent' for e in starts)
    assert ends[0].data.status == 'accepted' and ends[0].data.stop_reason == 'tool_use'
    assert ends[0].data.tool_call_ids == ('probe-1',)
    assert (ends[0].data.input_tokens, ends[0].data.output_tokens,
            ends[0].data.cache_read_tokens) == (11, 3, 6)
    assert ends[1].data.stop_reason == 'stop' and ends[1].data.input_tokens is None
    tool_start, tool_end = events[3], events[4]
    assert tool_start.data.tool_call_id == tool_end.data.tool_call_id == 'probe-1'
    assert tool_start.data.boundary == tool_end.data.boundary == 'dispatch'
    assert tool_end.data.outcome == 'success'
    assert result['messages'][-1].content == 'PRIVATE_FINAL'
    assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).content == 'PRIVATE_OUTPUT'
    with pytest.raises(FrozenInstanceError):
        tool_end.data.outcome = 'exception'
    assert 'PRIVATE_' not in caplog.text + repr(events)
    logs = [r.application_event for r in caplog.records if hasattr(r, 'application_event')
            and r.application_event['event'] in CALL_METADATA_EVENTS]
    assert len(logs) == 8 and 'PRIVATE_' not in repr(logs)


@pytest.mark.parametrize('kind', ['timeout', 'rejected'])
@async_test
async def test_model_failure_has_no_accepted_end_and_preserves_exception(monkeypatch, kind, caplog):
    failure = TimeoutError('PRIVATE_TIMEOUT')

    class FailingModel(ToolModel):
        def _generate(self, *args, **kwargs):
            raise failure

    model = (FailingModel(responses=[AIMessage(content='unused')]) if kind == 'timeout'
             else ToolModel(responses=[AIMessage(content='PRIVATE_FILTERED',
                                               response_metadata={'finish_reason': 'content_filter'})]))
    app = await make_app(monkeypatch, model)
    events = []
    app.subscribe_events(call_metadata_only(events.append))
    expected = TimeoutError if kind == 'timeout' else ModelResponseRejected
    with caplog.at_level('INFO'), pytest.raises(expected) as caught:
        await app.arun(await app.acreate_session('A'), [('user', 'PRIVATE_PROMPT')])
    if kind == 'timeout':
        assert caught.value is failure
    assert [e.event_type for e in events] == [
        'run_started', 'model_call_started', 'model_call_finished', 'run_finished',
    ]
    end = events[2].data
    assert end.status == 'failed' and end.failure is not None
    assert end.failure.reason == ('timeout' if kind == 'timeout' else 'stop_reason_error')
    assert events[-1].data.status == 'failed'
    assert 'PRIVATE_' not in repr(events) + caplog.text


@async_test
async def test_midrun_observer_failure_does_not_change_model_or_tool_result(monkeypatch):
    app = await make_app(monkeypatch)
    observed, broken_calls = [], []

    def broken(event):
        broken_calls.append(event.event_type)
        if event.event_type == 'model_call_started':
            raise RuntimeError('PRIVATE_OBSERVER')

    app.subscribe_events(call_metadata_only(broken))
    app.subscribe_events(call_metadata_only(observed.append))
    result = await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert broken_calls == ['run_started', 'model_call_started']
    assert observed[-1].data.status == 'completed' and len(observed) == 4


@async_test
async def test_bounded_input_recovery_is_one_logical_model_call(monkeypatch):
    from test_context_overflow_recovery import _app, _configured_model, _history, _status

    model = _configured_model([_status(), AIMessage(content='recovered')])
    app = await _app(monkeypatch, {'A': model})
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), _history())
    assert result['messages'][-1].content == 'recovered'
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1
    assert len(events_of(events, 'model_call_started')) == 1
    assert len(events_of(events, 'model_call_finished')) == 1
    assert events_of(events, 'model_call_finished')[0].data.status == 'accepted'


@async_test
async def test_legacy_and_mismatched_scope_do_not_invent_application_events():
    from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services

    model = ToolModel(responses=[AIMessage(content='done')])
    _runtime_registry_1 = prepare_tool_registry([])
    _runtime_names_1 = _runtime_registry_1.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
        resolve_model=lambda _id, agent_path='root': ('A', model), record_selection=lambda *_args: None,
    )
    events = []
    subscriptions = ApplicationEventSubscriptions()
    subscriptions.subscribe(events.append)
    publisher = subscriptions.publisher('outer-session', 'outer-run')
    publisher.publish('run_started', RunStartedData('message'))
    with bind_run_services(RunServices(publisher=publisher)):
        result = (await ainvoke_agent_run(agent, [('user', 'run')], session_id='legacy-session',
                                  thread_id='legacy-thread', run_id='legacy-run'))
    publisher.publish('run_finished', RunFinishedData('completed'))
    assert result['messages'][-1].content == 'done'
    assert [e.event_type for e in events] == ['run_started', 'run_finished']


@async_test
async def test_failed_run_binding_is_reset_before_next_direct_invocation(monkeypatch):
    from naiyue_test_agent.agent_runtime.run_services import current_run_services

    failure = TimeoutError('synthetic')

    class FailingModel(ToolModel):
        def _generate(self, *args, **kwargs):
            raise failure

    app = await make_app(monkeypatch, FailingModel(responses=[AIMessage(content='unused')]))
    with pytest.raises(TimeoutError):
        await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert current_run_services() is None


@async_test
async def test_partial_invalid_tool_response_is_failed_before_dispatch(monkeypatch):
    called = []

    @tool
    def probe() -> str:
        """Must not execute from a rejected response."""
        called.append(True)
        return 'unexpected'

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'valid'},
    ], invalid_tool_calls=[{'name': 'probe', 'args': '{', 'id': 'invalid', 'error': 'parse'}])])
    app = await make_app(monkeypatch, model, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(ModelResponseRejected) as caught:
        await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert caught.value.reason == 'invalid_tool_calls' and called == []
    assert events_of(events, 'model_call_finished')[0].data.status == 'failed'
    assert events_of(events, 'tool_call_started') == []
