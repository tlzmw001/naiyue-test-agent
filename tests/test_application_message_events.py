"""Complete messages observed from actual compiled agent state."""

import asyncio

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import json
import logging
from threading import Barrier, Event

import pytest
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from naiyue_test_agent import application
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog
from test_application_run_events import ToolModel, make_app


def messages(events):
    return [e for e in events if e.event_type in ('message_completed', 'message_revised')]


def rows(events):
    return [json.loads(e.data.snapshot_json) for e in messages(events)]


@async_test
async def test_tool_loop_publishes_all_content_once_and_default_logs_are_metadata(monkeypatch, caplog):
    called = []

    @tool
    def probe(value: str) -> str:
        """Return a synthetic value."""
        called.append(value)
        return 'PRIVATE_TOOL'

    model = ToolModel(responses=[AIMessage(content='PRIVATE_THOUGHT', tool_calls=[
        {'name': 'probe', 'args': {'value': 'PRIVATE_ARG'}, 'id': 'c1'},
    ]), AIMessage(content='PRIVATE_ANSWER')])
    app = await make_app(monkeypatch, model, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(events.append)
    with caplog.at_level(logging.INFO):
        result = await app.arun(await app.acreate_session('A'), [('user', 'PRIVATE_INPUT')])
    assert called == ['PRIVATE_ARG'] and model.i == 0
    assert [row['type'] for row in rows(events)] == ['human', 'ai', 'tool', 'ai']
    assert [row['content'] for row in rows(events)] == [m.content for m in result['messages']]
    assert [e.data.message_id for e in messages(events)] == [m.id for m in result['messages']]
    assert rows(events)[1]['tool_calls'][0]['id'] == rows(events)[2]['tool_call_id'] == 'c1'
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    kinds = [e.event_type for e in events]
    assert kinds.index('model_call_finished') < kinds.index('message_completed', 3)
    assert kinds[-1] == 'run_finished' and events[-1].data.status == 'completed'
    records = [r.application_event for r in caplog.records if hasattr(r, 'application_event')]
    assert len(records) == len(events)
    assert 'PRIVATE_' not in repr(records) + caplog.text
    assert all('snapshot_json' not in row and 'snapshot_sha256' not in row for row in records)


@async_test
async def test_next_run_skips_old_history_and_changed_input_id_is_revised(monkeypatch):
    app = await make_app(monkeypatch, ToolModel(responses=[AIMessage(content='one'), AIMessage(content='two')]))
    session = await app.acreate_session('A')
    events = []
    app.subscribe_events(events.append)
    first = await app.arun(session, [HumanMessage(id='input', content='before')])
    events.clear()
    second = await app.arun(session, [HumanMessage(id='input', content='after')])
    assert [e.event_type for e in messages(events)] == ['message_revised', 'message_completed']
    assert [row['content'] for row in rows(events)] == ['after', 'two']
    assert first['messages'][1].id == second['messages'][1].id


@async_test
async def test_approval_resume_preserves_interrupt_and_does_not_repeat_request(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('must not read', encoding='utf-8')
    app = await make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'approval'},
    ]), AIMessage(content='rejected handled')]), [create_session_read_tool()])
    session = await app.acreate_workspace_session('A', root)
    events = []
    app.subscribe_events(events.append)
    paused = await app.arun(session, [('user', 'read')])
    assert paused['__interrupt__'] and paused['approval_requests']
    assert [row['type'] for row in rows(events)] == ['human', 'ai']
    pending_id = rows(events)[-1]['id']
    events.clear()
    resumed = await app.aresume_tool_approval(session, ['reject'])
    assert [row['type'] for row in rows(events)] == ['tool', 'ai']
    assert rows(events)[0]['status'] == 'error' and rows(events)[0]['tool_call_id'] == 'approval'
    assert resumed['messages'][1].id == pending_id
    assert events[-1].data.status == 'completed'


@async_test
async def test_structured_response_object_and_artificial_tool_message_are_preserved(monkeypatch):
    class Answer(BaseModel):
        value: str

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'Answer', 'args': {'value': 'structured'}, 'id': 'schema'},
    ])])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    app = await build_application('synthetic.json', tools=[], system_prompt='',
                                               response_format=ToolStrategy(Answer))
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), [('user', 'answer')])
    assert isinstance(result['structured_response'], Answer)
    assert result['structured_response'].value == 'structured'
    assert [row['type'] for row in rows(events)] == ['human', 'ai', 'tool']
    assert rows(events)[-1]['tool_call_id'] == 'schema'
    assert not any(e.event_type == 'tool_call_started' for e in events)


@async_test
async def test_compaction_summary_and_removals_are_observed_without_reemitting_kept_history(monkeypatch):
    from test_context_compaction import _app, _model

    a = _model([AIMessage(content='one'), AIMessage(content='two')], window=10000)
    b = _model([AIMessage(content='three')], window=300, output=10, reserve=250, keep=10,
               summaries=[AIMessage(content='summary')], model_id='B')
    app = await _app(monkeypatch, {'A': a, 'B': b})
    session = await app.acreate_session('A')
    first = await app.arun(session, [('user', 'FIRST_' + 'x' * 100)])
    second = await app.arun(session, [('user', 'SECOND_' + 'y' * 100)])
    await app.aselect_model(session, 'B')
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(session, [('user', 'third')])
    assert [row['content'] for row in rows(events)][0] == 'third'
    assert len(messages(events)) == 3
    assert any(e.data.is_context_summary for e in messages(events))
    change = next(e.data for e in events if e.event_type == 'context_messages_changed')
    assert change.reason == 'compaction'
    assert change.removed_message_ids == tuple(m.id for m in first['messages'])
    assert change.current_message_ids == tuple(m.id for m in result['messages'][:-1])
    assert not set(m.id for m in second['messages'][2:]) & set(e.data.message_id for e in messages(events))


@async_test
async def test_follow_up_becomes_message_only_after_queue_delivery(monkeypatch):
    holder = {}
    queued = []

    class Queueing(ToolModel):
        def _generate(self, *args, **kwargs):
            if not queued:
                queued.append(True)
                holder['app'].follow_up(holder['session'], 'follow')
                assert [row['content'] for row in rows(holder['events'])] == ['initial']
            return super()._generate(*args, **kwargs)

    app = await make_app(monkeypatch, Queueing(responses=[AIMessage(content='first'), AIMessage(content='second')]))
    session = await app.acreate_session('A')
    events = []
    app.subscribe_events(events.append)
    holder.update(app=app, session=session, events=events)
    result = await app.arun(session, [('user', 'initial')])
    assert [row['content'] for row in rows(events)] == ['initial', 'first', 'follow', 'second']
    assert [row['id'] for row in rows(events)] == [m.id for m in result['messages']]


@pytest.mark.parametrize('control', ['steering', 'stop'])
@async_test
async def test_control_during_tool_retains_all_messages_and_original_stop_result(monkeypatch, control):
    entered, release = Event(), Event()

    @tool
    def blocked() -> str:
        """Block until the test delivers run control."""
        entered.set()
        assert release.wait(timeout=5)
        return 'tool body'

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'blocked', 'args': {}, 'id': 'blocked'},
    ]), AIMessage(content='answer')])
    app = await make_app(monkeypatch, model, [ToolSpec(blocked, blocked.args_schema, permissions=ToolPermission(default='allow'))])
    session = await app.acreate_session('A')
    events, outcomes = [], []
    app.subscribe_events(events.append)

    async def run():
        try:
            outcomes.append(await app.arun(session, [('user', 'initial')]))
        except BaseException as error:
            outcomes.append(error)

    worker = asyncio.create_task(run())

    try:
        assert await asyncio.to_thread(entered.wait, timeout=5)
        if control == 'steering':
            app.steer(session, 'steer')
            assert 'steer' not in [row['content'] for row in rows(events)]
        else:
            app.request_stop(session)
        release.set()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(worker), 5)
    assert worker.done() and len(outcomes) == 1 and isinstance(outcomes[0], dict), outcomes
    result = outcomes[0]
    expected = ['human', 'ai', 'tool', 'human', 'ai'] if control == 'steering' else ['human', 'ai', 'tool']
    assert [row['type'] for row in rows(events)] == expected
    assert [row['content'] for row in rows(events)] == [m.content for m in result['messages']]
    assert events[-1].data.status == ('completed' if control == 'steering' else 'stopped')
    assert result['run_stopped'] is (control == 'stop')


@async_test
async def test_parallel_tool_messages_are_each_delivered_once(monkeypatch):
    barrier = Barrier(2)

    @tool
    def parallel(value: str) -> str:
        """Wait for both calls to enter."""
        barrier.wait(timeout=5)
        return value

    app = await make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'parallel', 'args': {'value': 'one'}, 'id': 'one'},
        {'name': 'parallel', 'args': {'value': 'two'}, 'id': 'two'},
    ]), AIMessage(content='done')]), [ToolSpec(parallel, parallel.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), [('user', 'run')])
    tools = [row for row in rows(events) if row['type'] == 'tool']
    assert [(row['tool_call_id'], row['content']) for row in tools] == [('one', 'one'), ('two', 'two')]
    assert [row['id'] for row in rows(events)] == [m.id for m in result['messages']]
    assert len(set(row['id'] for row in rows(events))) == 5


@async_test
async def test_input_overflow_only_publishes_committed_summary_and_not_summary_ai(monkeypatch):
    from test_context_overflow_recovery import _app, _configured_model, _history, _status

    model = _configured_model([_status(), AIMessage(content='recovered')])
    app = await _app(monkeypatch, {'A': model})
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), _history())
    completed = messages(events)
    assert sum(e.data.is_context_summary for e in completed) == 1
    assert len([row for row in rows(events) if row['type'] == 'ai' and row['content'] == 'recovered']) == 1
    assert sum(e.event_type == 'model_call_started' for e in events) == 1
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1
    assert result['messages'][0].id == next(e.data.message_id for e in completed if e.data.is_context_summary)
    assert any(e.event_type == 'context_messages_changed' for e in events)


@async_test
async def test_complete_message_visibility_is_not_saver_confirmation(monkeypatch):
    save_entered, release_save, ai_seen = Event(), Event(), Event()

    class LaggingSaver(InMemorySaver):
        async def aput(self, config, checkpoint, metadata, new_versions):
            if any(isinstance(m, AIMessage) for m in checkpoint['channel_values'].get('messages', [])):
                save_entered.set()
                assert await asyncio.to_thread(release_save.wait, timeout=5)
            return await super().aput(config, checkpoint, metadata, new_versions)

    saver = LaggingSaver()
    app = await make_app(monkeypatch, ToolModel(responses=[AIMessage(content='answer')]))
    app._agent.checkpointer = saver
    session = await app.acreate_session('A')
    events, outcomes = [], []

    def observe(event):
        events.append(event)
        if event.event_type == 'message_completed' and event.data.message_type == 'ai':
            ai_seen.set()

    app.subscribe_events(observe)

    async def run():
        try:
            outcomes.append(await app.arun(session, [('user', 'run')]))
        except BaseException as error:
            outcomes.append(error)

    worker = asyncio.create_task(run())

    config = {'configurable': {'thread_id': app._sessions.get_thread_id(session)}}
    try:
        assert await asyncio.to_thread(ai_seen.wait, timeout=5) and await asyncio.to_thread(save_entered.wait, timeout=5)
        saved = saver.get_tuple(config)
        assert saved is not None
        assert not any(isinstance(m, AIMessage) for m in saved.checkpoint['channel_values']['messages'])
        assert not any(e.event_type == 'run_finished' for e in events)
    finally:
        release_save.set()
        await asyncio.wait_for(asyncio.shield(worker), 5)
    assert worker.done() and len(outcomes) == 1 and isinstance(outcomes[0], dict), outcomes
    assert events[-1].data.status == 'completed'
    assert any(isinstance(m, AIMessage) for m in saver.get_tuple(config).checkpoint['channel_values']['messages'])
