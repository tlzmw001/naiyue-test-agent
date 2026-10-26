"""Every formal model request prepares one fixed configuration before execution."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage

from native_application_helpers import async_test
from event_sequence import EventSequenceChecker
from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission
from test_context_compaction import _app, _model
from test_dynamic_tools import definition
from test_prompt_cache_call_paths import cached_application, completion_response


@pytest.mark.parametrize('context', [None, {}])
@async_test
async def test_missing_session_rejects_before_resolving_configuration(context):
    from naiyue_test_agent.agent_runtime import make_model_selection_middleware
    calls = []
    middleware = make_model_selection_middleware(lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='non-empty session_id'):
        await middleware.abefore_model({}, SimpleNamespace(context=context))
    assert calls == []


def history():
    return [HumanMessage(content='old fact ' * 400, id='old-user'),
            AIMessage(content='recorded', id='old-answer'),
            HumanMessage(content='continue with the latest task', id='latest')]


def small_window(model):
    model.metadata = {**model.metadata, 'naiyue_model_limits': {
        'model_id': 'root', 'context_window_tokens': 4000, 'max_output_tokens': 1024,
        'context_window_source': 'config', 'max_output_source': 'config'},
        'naiyue_compaction_reserve': {'tokens': 3500, 'source': 'config'},
        'naiyue_compaction_keep_recent': {'tokens': 8, 'source': 'config'}}


@async_test
async def test_session_reads_model_thinking_and_tool_names_in_one_configuration(monkeypatch):
    model = _model([], window=10000)
    app = await _app(monkeypatch, {'A': model}, tools=[definition()])
    try:
        sid = await app.host.sessions.create('A')
        await app.host.sessions.select_tools(sid, ['probe'])
        configuration = app.host.sessions._sessions.require(sid).resolve_request_configuration()
        assert configuration == ('A', model, ('probe',))
    finally:
        await app.aclose()


@async_test
async def test_tool_result_growth_triggers_compaction_before_next_request(monkeypatch, caplog):
    effects = []

    @tool
    def grow() -> str:
        """Return controlled history growth."""
        effects.append('grow')
        return 'tool growth ' * 500

    model = _model([AIMessage(content='', tool_calls=[{'name': 'grow', 'args': {}, 'id': 'grow-1'}]),
                    AIMessage(content='done')], window=8000, output=100, reserve=7400, keep=20,
                    summaries=[AIMessage(content='earlier fact')])
    app = await _app(monkeypatch, {'A': model}, tools=[ToolSpec(grow, grow.args_schema,
        permissions=ToolPermission(default='allow'))])
    events = []
    try:
        app.host.observability.subscribe(events.append)
        result = await app.host.runs.start(await app.host.sessions.create('A'), [
            HumanMessage(content='earlier ' * 70, id='old'), AIMessage(content='recorded', id='reply'),
            HumanMessage(content='run the tool', id='task')])
        assert result['messages'][-1].content == 'done' and effects == ['grow']
        assert len(model.normal_inputs) == 2
        assert len(model.summary_inputs) == 1
        assert result['messages'][0].additional_kwargs['naiyue_compaction_summary'] is True
        starts = [e for e in events if e.event_type == 'model_call_started']
        assert [e.data.purpose for e in starts] == ['agent', 'summary', 'agent']
        turns = [e for e in events if e.event_type == 'turn_started']
        operation = next(e for e in events if e.event_type == 'compaction_started')
        assert turns[1].sequence < operation.sequence < starts[-1].sequence
        assert turns[1].data.model_call_id == operation.data.model_call_id
        EventSequenceChecker.assert_healthy(events, caplog.records)
    finally:
        await app.aclose()


@async_test
async def test_preparation_summary_belongs_to_the_turn_before_main_callback(monkeypatch, tmp_path, caplog):
    events = []

    def serve(request):
        return completion_response(json.loads(request.content), text='done')

    async with cached_application(monkeypatch, tmp_path, serve) as (app, catalog):
        small_window(catalog.get('root'))
        app.host.observability.subscribe(events.append)
        await app.host.runs.start(await app.host.sessions.create('root'), history())
    turn = next(e for e in events if e.event_type == 'turn_started')
    operation = next(e for e in events if e.event_type == 'compaction_started')
    assert turn.sequence < operation.sequence
    assert operation.data.model_call_id == turn.data.model_call_id
    starts = [e for e in events if e.event_type == 'model_call_started']
    assert [e.data.purpose for e in starts] == ['summary', 'agent']
    assert all(e.data.invocation_id != turn.data.model_call_id for e in starts)
    EventSequenceChecker.assert_healthy(events, caplog.records)


@pytest.mark.parametrize('stage', ['configuration', 'tools'])
@async_test
async def test_preparation_failure_preserves_error_and_closes_turn_before_model(monkeypatch, caplog, stage):
    from naiyue_test_agent.agent_runtime.tool_execution.toolset import ToolSetMiddleware

    failure = RuntimeError('PRIVATE_PREPARATION')
    model = _model([AIMessage(content='must not run')], window=10000)
    app = await _app(monkeypatch, {'A': model})
    events = []
    try:
        sid = await app.host.sessions.create('A')
        actor = app.host.sessions._sessions.require(sid)

        def fail(*args, **kwargs):
            raise failure

        if stage == 'configuration':
            monkeypatch.setattr(actor, 'resolve_request_configuration', fail)
        else:
            monkeypatch.setattr(ToolSetMiddleware, 'prepare_tool_view', fail)
        app.host.observability.subscribe(events.append)
        with pytest.raises(RuntimeError) as caught:
            await app.host.runs.start(sid, [('user', 'prepare')])
        assert caught.value is failure
        assert model.normal_inputs == model.summary_inputs == []
        turns = [e for e in events if e.event_type in ('turn_started', 'turn_finished')]
        assert [e.event_type for e in turns] == ['turn_started', 'turn_finished']
        assert turns[-1].data.status == 'failed'
        assert not any(e.event_type == 'model_call_started' for e in events)
        assert events[-1].data.status == 'failed' and not actor.is_busy()
        assert 'PRIVATE_PREPARATION' not in repr(events) + caplog.text
        assert 'model_request_preparation_failed' in caplog.text
        assert not any(hasattr(record, 'model_failure') for record in caplog.records)
        EventSequenceChecker.assert_healthy(events, caplog.records)
    finally:
        await app.aclose()


@pytest.mark.parametrize('same_run', [True, False])
@async_test
async def test_summary_time_configuration_change_applies_only_to_next_request(monkeypatch, tmp_path, same_run):
    entered, release = asyncio.Event(), asyncio.Event()
    sent, effects = [], []

    async def serve(request):
        body = json.loads(request.content)
        sent.append(body)
        if body['messages'][0]['content'].startswith('Summarize completed'):
            entered.set()
            await release.wait()
        elif same_run and body['model'] == 'root':
            return completion_response(body, call=('first', {'value': 'fixed'}, 'fixed-call'))
        return completion_response(body, text='done')

    async with cached_application(monkeypatch, tmp_path, serve,
            tools=[definition('first', calls=effects), definition('second')]) as (app, catalog):
        small_window(catalog.get('root'))
        sid = await app.host.sessions.create('root')
        await app.host.sessions.select_thinking(sid, 'root', 'low')
        await app.host.sessions.select_tools(sid, ['first'])
        task = asyncio.create_task(app.host.runs.start(sid, history()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await app.host.sessions.select_model(sid, 'child')
            await app.host.sessions.select_tools(sid, ['second'])
        finally:
            release.set()
        assert (await task)['messages'][-1].content == 'done'
        if not same_run:
            assert (await app.host.runs.start(sid, [('user', 'next request')]))['messages'][-1].content == 'done'
    summary, first, second = sent
    assert summary['model'] == first['model'] == 'root' and second['model'] == 'child'
    assert summary['reasoning_effort'] == first['reasoning_effort'] == 'low'
    assert 'reasoning_effort' not in second
    assert [t['function']['name'] for t in first['tools']] == ['first']
    assert [t['function']['name'] for t in second['tools']] == ['second']
    assert effects == ([('first', 'fixed')] if same_run else [])


@pytest.mark.parametrize('cancel', [False, True])
@async_test
async def test_stop_or_cancel_in_preparation_closes_started_turn_without_main_call(monkeypatch, tmp_path, cancel):
    entered, release = asyncio.Event(), asyncio.Event()
    events = []

    async def serve(request):
        entered.set()
        await release.wait()
        return completion_response(json.loads(request.content), text='summary')

    async with cached_application(monkeypatch, tmp_path, serve) as (app, catalog):
        small_window(catalog.get('root'))
        app.host.observability.subscribe(events.append)
        sid = await app.host.sessions.create('root')
        task = asyncio.create_task(app.host.runs.start(sid, history()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                app.host.runs.stop(sid)
                assert (await asyncio.wait_for(task, 5))['run_stopped'] is True
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        turns = [e for e in events if e.event_type in ('turn_started', 'turn_finished')]
        assert [e.event_type for e in turns] == ['turn_started', 'turn_finished']
        assert turns[-1].data.status == 'failed'
        assert turns[-1].data.failure.reason == ('task_cancelled' if cancel else 'user_stop')
        assert [e.data.purpose for e in events if e.event_type == 'model_call_started'] == ['summary']
        assert not app.host.sessions._sessions.require(sid).is_busy()
