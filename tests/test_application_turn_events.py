"""Turn lifecycle reflects the actual model/tool/approval graph."""

import json
import logging
from threading import Barrier

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.agent_runtime.tools import create_session_read_tool
from test_application_run_events import ToolModel, make_app


def turns(events, kind=None):
    return [e for e in events if e.event_type.startswith('turn_')
            and (kind is None or e.event_type == kind)]


def test_text_response_is_one_turn_inside_run_and_finishes_after_message(monkeypatch, caplog):
    app = make_app(monkeypatch)
    events = []
    app.subscribe_events(events.append)
    with caplog.at_level(logging.INFO):
        result = app.run(app.create_session('A'), [('user', 'PRIVATE_INPUT')])
    assert [e.event_type for e in turns(events)] == ['turn_started', 'turn_finished']
    start, end = turns(events)
    assert start.data.turn_id == end.data.turn_id
    assert start.data.origin_run_id == end.data.origin_run_id == start.run_id
    assert end.data.assistant_message_id == result['messages'][-1].id
    assert end.data.status == 'completed' and end.data.tool_message_ids == ()
    model_start = next(e for e in events if e.event_type == 'model_call_started')
    assert start.sequence < model_start.sequence and start.data.model_call_id == model_start.data.model_call_id
    message_end = next(e for e in events if e.event_type == 'message_completed' and e.data.message_type == 'ai')
    assert message_end.sequence < end.sequence < events[-1].sequence
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert 'PRIVATE_' not in caplog.text + repr(turns(events))


@pytest.mark.parametrize('mode', ['sequential', 'parallel'])
def test_one_tool_batch_and_final_answer_are_two_turns(monkeypatch, mode):
    barrier = Barrier(2) if mode == 'parallel' else None

    @tool
    def probe(value: str) -> str:
        """Return a synthetic value."""
        if barrier:
            barrier.wait(timeout=5)
        return value

    probe.metadata = {'naiyue_execution_mode': mode}
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {'value': 'one'}, 'id': 'one'},
        {'name': 'probe', 'args': {'value': 'two'}, 'id': 'two'},
    ]), AIMessage(content='done')])
    app = make_app(monkeypatch, model, [probe])
    events = []
    app.subscribe_events(events.append)
    result = app.run(app.create_session('A'), [('user', 'run')])
    assert [e.event_type for e in turns(events)] == ['turn_started', 'turn_finished'] * 2
    first, second = turns(events, 'turn_finished')
    assert first.data.tool_call_ids == ('one', 'two')
    assert first.data.tool_message_ids == tuple(m.id for m in result['messages'] if m.type == 'tool')
    assert first.data.error_result_count == 0
    assert first.data.turn_id != second.data.turn_id
    assert first.sequence < turns(events, 'turn_started')[1].sequence


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_approval_pauses_and_resumes_same_turn_then_starts_next(monkeypatch, tmp_path, decision):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('PRIVATE_BODY', encoding='utf-8')
    app = make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read'},
    ]), AIMessage(content='done')]), [create_session_read_tool()])
    events = []
    app.subscribe_events(events.append)
    session = app.create_workspace_session('A', root)
    paused = app.run(session, [('user', 'read')])
    assert paused['approval_requests']
    assert [e.event_type for e in turns(events)] == ['turn_started', 'turn_suspended']
    first = turns(events)[0]
    state = app._sessions._sessions[session]
    saved = json.loads(json.dumps(state['pending_turn']))
    assert saved['turn_id'] == first.data.turn_id and saved['status'] == 'suspended'
    assert 'PRIVATE_' not in repr(saved)
    result = app.resume_tool_approval(session, [decision])
    resumed = turns(events, 'turn_resumed')[0]
    assert resumed.run_id != first.run_id
    assert resumed.data.turn_id == first.data.turn_id and resumed.data.origin_run_id == first.run_id
    assert resumed.data.model_call_id == first.data.model_call_id
    finishes = turns(events, 'turn_finished')
    assert finishes[0].data.turn_id == first.data.turn_id and finishes[0].data.status == 'completed'
    assert finishes[0].data.error_result_count == (1 if decision == 'reject' else 0)
    assert finishes[0].data.tool_message_ids == (next(m.id for m in result['messages'] if m.type == 'tool'),)
    assert state['pending_turn'] is None and state['pending_tool_approval'] is None


def test_recoverable_tool_error_completes_turn_with_error_result_count(monkeypatch):
    @tool
    def probe() -> str:
        """Return a recoverable error."""
        raise ToolException('Invalid path; choose an existing file.')

    probe.handle_tool_error = True
    app = make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'probe'},
    ]), AIMessage(content='handled')]), [probe])
    events = []
    app.subscribe_events(events.append)
    result = app.run(app.create_session('A'), [('user', 'run')])
    end = turns(events, 'turn_finished')[0].data
    assert end.status == 'completed' and end.error_result_count == 1 and end.failure is None
    assert result['messages'][-1].content == 'handled'


def test_limited_input_recovery_is_one_logical_turn(monkeypatch):
    from test_context_overflow_recovery import _app, _configured_model, _history, _status

    model = _configured_model([_status(), AIMessage(content='recovered')])
    app = _app(monkeypatch, {'A': model})
    events = []
    app.subscribe_events(events.append)
    result = app.run(app.create_session('A'), _history())
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1
    assert [e.event_type for e in turns(events)] == ['turn_started', 'turn_finished']
    assert turns(events)[-1].data.assistant_message_id == result['messages'][-1].id


def test_steering_and_follow_up_create_turns_when_delivered_not_when_queued(monkeypatch):
    holder = {}

    @tool
    def probe() -> str:
        """Queue control while a tool is executing."""
        holder['app'].steer(holder['session'], 'steer')
        holder['app'].follow_up(holder['session'], 'follow')
        assert len(turns(holder['events'], 'turn_started')) == 1
        return 'tool body'

    app = make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'probe'},
    ]), AIMessage(content='second'), AIMessage(content='third')]), [probe])
    session = app.create_session('A')
    events = []
    app.subscribe_events(events.append)
    holder.update(app=app, session=session, events=events)
    result = app.run(session, [('user', 'initial')])
    assert [m.content for m in result['messages'] if m.type == 'human'] == ['initial', 'steer', 'follow']
    ends = turns(events, 'turn_finished')
    assert len(ends) == 3 and all(e.data.status == 'completed' for e in ends)
    assert [e.data.assistant_message_id for e in ends] == [m.id for m in result['messages'] if m.type == 'ai']
    assert len({e.data.turn_id for e in ends}) == 3 and len({e.run_id for e in ends}) == 1


def test_after_turn_stop_completes_current_turn_and_stops_run(monkeypatch):
    holder = {}

    @tool
    def stop_probe() -> str:
        """Request stop at the current turn boundary."""
        holder['app'].request_stop_after_turn(holder['session'])
        return 'done'

    app = make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'stop_probe', 'args': {}, 'id': 'stop'},
    ]), AIMessage(content='must not generate')]), [stop_probe])
    session = app.create_session('A')
    events = []
    app.subscribe_events(events.append)
    holder.update(app=app, session=session)
    result = app.run(session, [('user', 'run')])
    assert [m.type for m in result['messages']] == ['human', 'ai', 'tool']
    assert result['run_stopped'] is True
    assert [e.event_type for e in turns(events)] == ['turn_started', 'turn_finished']
    assert turns(events)[-1].data.status == 'completed' and events[-1].data.status == 'stopped'
    assert sum(e.event_type == 'model_call_started' for e in events) == 1


def test_structured_artificial_tool_result_completes_without_executor_event(monkeypatch):
    from langchain.agents.structured_output import ToolStrategy
    from pydantic import BaseModel
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog

    class Answer(BaseModel):
        value: str

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'Answer', 'args': {'value': 'structured'}, 'id': 'schema'},
    ])])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    app = application.build_local_application('synthetic.json', tools=[], system_prompt='',
                                               response_format=ToolStrategy(Answer))
    events = []
    app.subscribe_events(events.append)
    result = app.run(app.create_session('A'), [('user', 'run')])
    assert isinstance(result['structured_response'], Answer) and result['structured_response'].value == 'structured'
    assert [e.event_type for e in turns(events)] == ['turn_started', 'turn_finished']
    end = turns(events)[-1].data
    assert end.status == 'completed' and end.tool_call_ids == ('schema',)
    assert end.tool_message_ids == (result['messages'][-1].id,)
    assert not any(e.event_type == 'tool_call_started' for e in events)


def test_truncated_tool_response_finishes_with_error_result_without_executor(monkeypatch):
    called = []

    @tool
    def probe() -> str:
        """Must not execute a truncated request."""
        called.append(True)
        return 'unexpected'

    app = make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'probe'},
    ], response_metadata={'finish_reason': 'length'}), AIMessage(content='handled')]), [probe])
    events = []
    app.subscribe_events(events.append)
    result = app.run(app.create_session('A'), [('user', 'run')])
    assert called == [] and result['messages'][-1].content == 'handled'
    first = turns(events, 'turn_finished')[0].data
    assert first.status == 'completed' and first.error_result_count == 1


def test_turns_do_not_replay_historical_assistant_messages_on_next_run(monkeypatch):
    app = make_app(monkeypatch, ToolModel(responses=[AIMessage(content='first'), AIMessage(content='second')]))
    events = []
    app.subscribe_events(events.append)
    session = app.create_session('A')
    first = app.run(session, [('user', 'first')])
    first_end = turns(events, 'turn_finished')[0]
    events.clear()
    second = app.run(session, [('user', 'second')])
    assert len(turns(events, 'turn_started')) == len(turns(events, 'turn_finished')) == 1
    end = turns(events, 'turn_finished')[0]
    assert end.data.turn_id != first_end.data.turn_id
    assert end.data.assistant_message_id == second['messages'][-1].id != first['messages'][-1].id
