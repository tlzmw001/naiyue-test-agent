"""Turn failures retain primary errors and approval-attempt identity."""

from runtime_context_helpers import RunIdentity, typed_context
import asyncio

from native_application_helpers import async_test

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.application import prepare_tool_registry

from threading import Barrier

import pytest
from tool_feedback_helpers import fail_tool_dispatch
from langchain.tools import tool
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent import application
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.tools.processes import ProcessCleanupError
from naiyue_test_agent.agent_runtime.turn_observation import TurnBoundaryMiddleware
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
from test_application_run_events import ToolModel, make_app
from test_application_turn_events import turns


@pytest.mark.parametrize('mode', ['timeout', 'invalid'])
@async_test
async def test_failed_model_has_failed_turn_without_visible_assistant(monkeypatch, mode):
    failure = TimeoutError('PRIVATE_ERROR')

    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise failure

    model = (Failing(responses=[AIMessage(content='unused')]) if mode == 'timeout' else ToolModel(
        responses=[AIMessage(content='PRIVATE_REJECTED', invalid_tool_calls=[
            {'name': 'probe', 'args': '{', 'id': 'invalid', 'error': 'parse'},
        ])]))
    app = await make_app(monkeypatch, model)
    events = []
    app.host.observability.subscribe(events.append)
    with pytest.raises(TimeoutError if mode == 'timeout' else ModelResponseRejected) as caught:
        await app.host.runs.start(await app.host.sessions.create('A'), [('user', 'run')])
    if mode == 'timeout':
        assert caught.value is failure
    assert [e.event_type for e in turns(events)] == ['turn_started', 'turn_finished']
    end = turns(events)[-1].data
    assert end.status == 'failed' and end.assistant_message_id is None and end.tool_message_ids == ()
    assert end.failure.reason == ('timeout' if mode == 'timeout' else 'invalid_tool_calls')
    assert 'PRIVATE_' not in repr(turns(events))
    assert events[-1].data.status == 'failed'


@async_test
async def test_after_model_error_does_not_report_earlier_visible_ai_as_completed(monkeypatch):
    failure = RuntimeError('PRIVATE_AFTER_MODEL')

    def fail_checks(self, state, runtime):
        raise failure

    monkeypatch.setattr(TurnBoundaryMiddleware, 'after_model', fail_checks)
    app = await make_app(monkeypatch)
    events = []
    app.host.observability.subscribe(events.append)
    with pytest.raises(RuntimeError) as caught:
        await app.host.runs.start(await app.host.sessions.create('A'), [('user', 'run')])
    assert caught.value is failure
    end = turns(events, 'turn_finished')[0].data
    ai = next(e.data for e in events if e.event_type == 'message_completed' and e.data.message_type == 'ai')
    assert end.assistant_message_id == ai.message_id and end.status == 'failed'
    assert not any(e.data.status == 'completed' for e in turns(events, 'turn_finished'))


@async_test
async def test_fatal_tool_partial_recovery_finishes_failed_with_observed_skip(monkeypatch):
    failure = RuntimeError('PRIVATE_FATAL')
    entered = []

    @tool
    def fatal() -> str:
        """Fail the ordered batch."""
        return 'unreachable after the dispatch fault'


    @tool
    def later() -> str:
        """Must not run after failure."""
        entered.append(True)
        return 'unexpected'

    fail_tool_dispatch(monkeypatch, 'fatal', failure)
    app = await make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'fatal', 'args': {}, 'id': 'fatal'},
        {'name': 'later', 'args': {}, 'id': 'later'},
    ])]), [ToolSpec(fatal, fatal.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow')), ToolSpec(later, later.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.host.observability.subscribe(events.append)
    with pytest.raises(RuntimeError) as caught:
        await app.host.runs.start(await app.host.sessions.create('A'), [('user', 'run')])
    assert caught.value is failure and entered == []
    end = turns(events, 'turn_finished')[0]
    recovered = next(e for e in events if e.event_type == 'message_completed' and e.data.message_type == 'tool')
    assert recovered.data.visibility == 'recovery_view' and recovered.data.tool_call_id == 'later'
    assert end.data.status == 'failed' and end.data.tool_call_ids == ('fatal', 'later')
    assert end.data.tool_message_ids == (recovered.data.message_id,) and end.data.error_result_count == 1
    assert recovered.sequence < end.sequence < events[-1].sequence


async def make_approval(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('body', encoding='utf-8')
    app = await make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read'},
    ]), AIMessage(content='done')]), [create_session_read_tool()])
    session = await app.host.sessions.create_workspace('A', root)
    events = []
    app.host.observability.subscribe(events.append)
    await app.host.runs.start(session, [('user', 'read')])
    return app, session, events


@async_test
async def test_prepare_failure_preserves_waiting_turn_for_later_resume(monkeypatch, tmp_path):
    from naiyue_test_agent.application.sessions import session_execution

    app, session, events = await make_approval(monkeypatch, tmp_path)
    saved = app.host.sessions._sessions._sessions[session].lifecycle.snapshot_for_run()['pending_turn']
    original = session_execution.prepare_run_resources
    failure = OSError('PRIVATE_PREPARE')

    def fail_prepare(*args, **kwargs):
        raise failure

    monkeypatch.setattr(session_execution, 'prepare_run_resources', fail_prepare)
    old_count = len(events)
    with pytest.raises(OSError) as caught:
        await app.host.runs.resume_approval(session, ['approve'])
    assert caught.value is failure and turns(events[old_count:]) == []
    assert app.host.sessions._sessions._sessions[session].lifecycle.snapshot_for_run()['pending_turn'] == saved
    assert events[-1].data.failure.stage == 'prepare'
    monkeypatch.setattr(session_execution, 'prepare_run_resources', original)
    await app.host.runs.resume_approval(session, ['reject'])
    assert turns(events, 'turn_resumed')[0].data.turn_id == saved['turn_id']


@async_test
async def test_failed_approval_tool_stage_requires_review_before_another_attempt(monkeypatch, tmp_path):
    from naiyue_test_agent.agent_runtime.tool_execution.batch import ToolBatchMiddleware

    original = ToolBatchMiddleware.awrap_tool_call
    failure = RuntimeError('PRIVATE_EXECUTE')
    attempts = []

    async def fail_once(self, request, handler):
        attempts.append(True)
        if len(attempts) == 1:
            raise failure
        return await original(self, request, handler)

    monkeypatch.setattr(ToolBatchMiddleware, 'awrap_tool_call', fail_once)
    # create_agent captures bound hooks during assembly; inject before construction.
    app, session, events = await make_approval(monkeypatch, tmp_path)
    suspended_id = turns(events, 'turn_started')[0].data.turn_id
    with pytest.raises(RuntimeError) as caught:
        await app.host.runs.resume_approval(session, ['approve'])
    assert caught.value is failure
    assert turns(events, 'turn_finished')[-1].data.status == 'failed'
    state = app.host.sessions._sessions._sessions[session]
    assert state.describe_session()['manual_review_required'] and state.lifecycle.snapshot_for_run()['pending_turn'] is None
    assert state.lifecycle.snapshot_for_run()['pending'] is None and not state.is_busy()
    thread_id = state.get_thread_id()
    assert (await app.host.runs._agent.aget_state({'configurable': {'thread_id': thread_id}})).next == ('tools',)
    count = len(events)
    with pytest.raises(application.SessionManualReviewRequiredError):
        await app.host.runs.resume_approval(session, ['approve'])
    with pytest.raises(application.SessionManualReviewRequiredError):
        await app.host.runs.start(session, [('user', 'retry')])
    assert events[count:] == [] and attempts == [True]
    assert [(e.data.turn_id, e.data.status) for e in turns(events, 'turn_finished')] == [(suspended_id, 'failed')]


@pytest.mark.parametrize('failure', [GraphBubbleUp(), KeyboardInterrupt()])
@async_test
async def test_framework_and_process_control_do_not_fabricate_failed_or_cancelled(monkeypatch, failure):
    app = await make_app(monkeypatch)
    from naiyue_test_agent.agent_runtime import async_invocation
    async def control(_agent, _input, *, context, config, publisher, **options):
        assert options == {'durability': 'sync'}
        from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
        current_run_scope().observation.turns.begin('control-call')
        raise failure
    # Inject at the awaited stream boundary after starting the actual TurnObserver.
    # This keeps KeyboardInterrupt outside asyncio's internal model tasks.
    monkeypatch.setattr(async_invocation, 'ainvoke_with_message_observation', control)
    events = []
    app.host.observability.subscribe(events.append)
    with pytest.raises(type(failure)) as caught:
        await app.host.runs.start(await app.host.sessions.create('A'), [('user', 'run')])
    assert caught.value is failure
    assert [e.event_type for e in turns(events)] == ['turn_started']
    assert not any(e.event_type == 'run_finished' for e in events)


@async_test
async def test_unknown_tool_outcome_is_failed_turn_and_manual_review_remains(monkeypatch):
    failure = ProcessCleanupError('PRIVATE_OUTCOME')

    @tool
    def uncertain() -> str:
        """Fail with uncertain side effects."""
        raise failure

    app = await make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'uncertain', 'args': {}, 'id': 'uncertain'},
    ])]), [ToolSpec(uncertain, uncertain.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.host.observability.subscribe(events.append)
    session = await app.host.sessions.create('A')
    with pytest.raises(ProcessCleanupError) as caught:
        await app.host.runs.start(session, [('user', 'run')])
    assert caught.value is failure
    assert turns(events, 'turn_finished')[0].data.failure.category == 'tool_outcome_unknown'
    assert events[-1].data.manual_review_required is True
    with pytest.raises(application.SessionManualReviewRequiredError):
        await app.host.runs.start(session, [('user', 'again')])


@async_test
async def test_two_sessions_share_graph_but_never_share_turn_identity(monkeypatch):
    barrier = Barrier(2)

    class Concurrent(ToolModel):
        def _generate(self, *args, **kwargs):
            barrier.wait(timeout=5)
            return super()._generate(*args, **kwargs)

    app = await make_app(monkeypatch, Concurrent(responses=[AIMessage(content='done')]))
    sessions = [await app.host.sessions.create('A') for _ in range(2)]
    events, results = [], []
    app.host.observability.subscribe(events.append)

    async def run(session):
        try:
            results.append(await app.host.runs.start(session, [('user', 'run')]))
        except BaseException as error:
            results.append(error)

    workers = [asyncio.create_task(run(session)) for session in sessions]
    for worker in workers:
        await asyncio.wait_for(asyncio.shield(worker), 6)
        assert worker.done()
    assert len(results) == 2 and all(isinstance(r, dict) for r in results), results
    starts = turns(events, 'turn_started')
    assert len({e.data.turn_id for e in starts}) == 2
    for session in sessions:
        scoped = turns([e for e in events if e.session_id == session])
        assert [e.event_type for e in scoped] == ['turn_started', 'turn_finished']
        assert scoped[0].data.turn_id == scoped[1].data.turn_id


@async_test
async def test_dynamic_runtime_has_no_app_observation_and_fits_explicit_step_budget():
    from naiyue_test_agent.agent_runtime import build_runtime_agent

    @tool
    def probe() -> str:
        """Return a synthetic value."""
        return 'value'

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'probe'},
    ]), AIMessage(content='done')])
    _runtime_registry_1 = prepare_tool_registry([ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])
    _runtime_names_1 = _runtime_registry_1.names()
    graph = build_runtime_agent(model, tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
                                resolve_model=lambda _s, agent_path='root': ('A', model), )
    result = (await graph.ainvoke({'messages': [('user', 'run')]}, context=typed_context(identity=RunIdentity(session_id='session')),
                          config={'recursion_limit': 14}))
    assert [m.type for m in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert result['messages'][-1].content == 'done'
    assert not any('TurnBoundaryMiddleware' in name for name in graph.nodes)


@async_test
async def test_observer_failure_on_turn_event_does_not_change_business_result(monkeypatch, caplog):
    app = await make_app(monkeypatch)
    calls, events = [], []

    def broken(event):
        calls.append(event.event_type)
        if event.event_type == 'turn_started':
            raise ValueError('PRIVATE_OBSERVER')

    app.host.observability.subscribe(broken)
    app.host.observability.subscribe(events.append)
    result = await app.host.runs.start(await app.host.sessions.create('A'), [('user', 'run')])
    assert calls == ['run_started', 'message_completed', 'turn_started']
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert turns(events, 'turn_finished')[0].data.status == 'completed'
    assert 'event_observer_failed' in caplog.text and 'PRIVATE_' not in caplog.text


@async_test
async def test_turn_reporting_secondary_error_does_not_mask_primary(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime.turn_observation import TurnObserver

    failure = TimeoutError('PRIVATE_PRIMARY')

    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise failure

    def fail_reporting(self, failure_data):
        raise ValueError('PRIVATE_SECONDARY')

    monkeypatch.setattr(TurnObserver, 'fail', fail_reporting)
    app = await make_app(monkeypatch, Failing(responses=[AIMessage(content='unused')]))
    events = []
    app.host.observability.subscribe(events.append)
    with pytest.raises(TimeoutError) as caught:
        await app.host.runs.start(await app.host.sessions.create('A'), [('user', 'run')])
    assert caught.value is failure and events[-1].data.status == 'failed'
    assert 'turn_failure_publish_failed' in caplog.text and 'PRIVATE_' not in caplog.text


@async_test
async def test_unsupported_state_observation_is_explicit_and_original_custom_result_kept(monkeypatch):
    app = await make_app(monkeypatch)
    original_result = {'messages': []}
    async def custom(*_a, **_kw):
        return original_result
    monkeypatch.setattr(app.host.runs._agent, 'ainvoke', custom)
    from naiyue_test_agent.application.observability.run_events import RunObservation
    from naiyue_test_agent.agent_runtime.async_invocation import ainvoke_agent_run
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher
    events = []
    observation = RunObservation(RunEventPublisher('session', 'run', (events.append,)), 'message')
    observation.start()
    with observation.bind(identity=RunIdentity(session_id='session', run_id='run')):
        assert await ainvoke_agent_run(app.host.runs._agent, [('user', 'run')], session_id='session',
            thread_id='custom', run_id='run') is original_result
    observation.result(original_result, None)
    observation.finish(False)
    assert [e.event_type for e in turns(events)] == ['turn_observation_unavailable']
    assert turns(events)[0].data.reason == 'state_observation_unavailable'
    assert events[-1].data.status == 'completed'
