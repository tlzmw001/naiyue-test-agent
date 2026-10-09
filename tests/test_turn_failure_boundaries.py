"""Turn failures retain primary errors and approval-attempt identity."""

from threading import Barrier, Thread

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.tools import create_session_read_tool
from naiyue_test_agent.agent_runtime.tools.bash import BashOutcomeUnknown
from naiyue_test_agent.agent_runtime.turn_observation import TurnBoundaryMiddleware
from naiyue_test_agent.model_access.errors import ModelResponseRejected
from test_application_run_events import ToolModel, make_app
from test_application_turn_events import turns


@pytest.mark.parametrize('mode', ['timeout', 'invalid'])
def test_failed_model_has_failed_turn_without_visible_assistant(monkeypatch, mode):
    failure = TimeoutError('PRIVATE_ERROR')

    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise failure

    model = (Failing(responses=[AIMessage(content='unused')]) if mode == 'timeout' else ToolModel(
        responses=[AIMessage(content='PRIVATE_REJECTED', invalid_tool_calls=[
            {'name': 'probe', 'args': '{', 'id': 'invalid', 'error': 'parse'},
        ])]))
    app = make_app(monkeypatch, model)
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(TimeoutError if mode == 'timeout' else ModelResponseRejected) as caught:
        app.run(app.create_session('A'), [('user', 'run')])
    if mode == 'timeout':
        assert caught.value is failure
    assert [e.event_type for e in turns(events)] == ['turn_started', 'turn_finished']
    end = turns(events)[-1].data
    assert end.status == 'failed' and end.assistant_message_id is None and end.tool_message_ids == ()
    assert end.failure.reason == ('timeout' if mode == 'timeout' else 'invalid_tool_calls')
    assert 'PRIVATE_' not in repr(turns(events))
    assert events[-1].data.status == 'failed'


def test_after_model_error_does_not_report_earlier_visible_ai_as_completed(monkeypatch):
    failure = RuntimeError('PRIVATE_AFTER_MODEL')

    def fail_checks(self, state, runtime):
        raise failure

    monkeypatch.setattr(TurnBoundaryMiddleware, 'after_model', fail_checks)
    app = make_app(monkeypatch)
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(RuntimeError) as caught:
        app.run(app.create_session('A'), [('user', 'run')])
    assert caught.value is failure
    end = turns(events, 'turn_finished')[0].data
    ai = next(e.data for e in events if e.event_type == 'message_completed' and e.data.message_type == 'ai')
    assert end.assistant_message_id == ai.message_id and end.status == 'failed'
    assert not any(e.data.status == 'completed' for e in turns(events, 'turn_finished'))


def test_fatal_tool_partial_recovery_finishes_failed_with_observed_skip(monkeypatch):
    failure = RuntimeError('PRIVATE_FATAL')
    entered = []

    @tool
    def fatal() -> str:
        """Fail the ordered batch."""
        raise failure

    fatal.metadata = {'naiyue_execution_mode': 'sequential'}

    @tool
    def later() -> str:
        """Must not run after failure."""
        entered.append(True)
        return 'unexpected'

    app = make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'fatal', 'args': {}, 'id': 'fatal'},
        {'name': 'later', 'args': {}, 'id': 'later'},
    ])]), [fatal, later])
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(RuntimeError) as caught:
        app.run(app.create_session('A'), [('user', 'run')])
    assert caught.value is failure and entered == []
    end = turns(events, 'turn_finished')[0]
    recovered = next(e for e in events if e.event_type == 'message_completed' and e.data.message_type == 'tool')
    assert recovered.data.visibility == 'recovery_view' and recovered.data.tool_call_id == 'later'
    assert end.data.status == 'failed' and end.data.tool_call_ids == ('fatal', 'later')
    assert end.data.tool_message_ids == (recovered.data.message_id,) and end.data.error_result_count == 1
    assert recovered.sequence < end.sequence < events[-1].sequence


def make_approval(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('body', encoding='utf-8')
    app = make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read'},
    ]), AIMessage(content='done')]), [create_session_read_tool()])
    session = app.create_workspace_session('A', root)
    events = []
    app.subscribe_events(events.append)
    app.run(session, [('user', 'read')])
    return app, session, events


def test_prepare_failure_preserves_waiting_turn_for_later_resume(monkeypatch, tmp_path):
    from naiyue_test_agent.application import session_service

    app, session, events = make_approval(monkeypatch, tmp_path)
    saved = app._sessions._sessions[session]['pending_turn']
    original = session_service.prepare_run_resources
    failure = OSError('PRIVATE_PREPARE')

    def fail_prepare(*args, **kwargs):
        raise failure

    monkeypatch.setattr(session_service, 'prepare_run_resources', fail_prepare)
    old_count = len(events)
    with pytest.raises(OSError) as caught:
        app.resume_tool_approval(session, ['approve'])
    assert caught.value is failure and turns(events[old_count:]) == []
    assert app._sessions._sessions[session]['pending_turn'] == saved
    assert events[-1].data.failure.stage == 'prepare'
    monkeypatch.setattr(session_service, 'prepare_run_resources', original)
    app.resume_tool_approval(session, ['reject'])
    assert turns(events, 'turn_resumed')[0].data.turn_id == saved['turn_id']


def test_failed_approval_execution_retry_is_new_turn_not_old_completed(monkeypatch, tmp_path):
    from naiyue_test_agent.agent_runtime.tool_batch import ToolBatchMiddleware

    original = ToolBatchMiddleware.wrap_tool_call
    failure = RuntimeError('PRIVATE_EXECUTE')
    attempts = []

    def fail_once(self, request, handler):
        attempts.append(True)
        if len(attempts) == 1:
            raise failure
        return original(self, request, handler)

    monkeypatch.setattr(ToolBatchMiddleware, 'wrap_tool_call', fail_once)
    # create_agent captures bound hooks during assembly; inject before construction.
    app, session, events = make_approval(monkeypatch, tmp_path)
    suspended_id = turns(events, 'turn_started')[0].data.turn_id
    with pytest.raises(RuntimeError) as caught:
        app.resume_tool_approval(session, ['approve'])
    assert caught.value is failure
    assert turns(events, 'turn_finished')[-1].data.status == 'failed'
    assert app._sessions._sessions[session]['pending_turn']['status'] == 'failed'
    assert app._sessions._sessions[session]['pending_turn']['post_model_checks_passed'] is True
    thread_id = app._sessions._sessions[session]['thread_id']
    assert app._agent.get_state({'configurable': {'thread_id': thread_id}}).next == ('tools',)
    result = app.resume_tool_approval(session, ['approve'])
    retry = next(e for e in turns(events, 'turn_started') if e.data.kind == 'approval_retry')
    assert retry.data.previous_turn_id == suspended_id and retry.data.turn_id != suspended_id
    ends = turns(events, 'turn_finished')
    assert [(e.data.turn_id, e.data.status) for e in ends][:2] == [
        (suspended_id, 'failed'), (retry.data.turn_id, 'completed'),
    ]
    assert result['messages'][-1].content == 'done'
    assert app._sessions._sessions[session]['pending_turn'] is None


@pytest.mark.parametrize('failure', [GraphBubbleUp(), KeyboardInterrupt()])
def test_framework_and_process_control_do_not_fabricate_failed_or_cancelled(monkeypatch, failure):
    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise failure

    app = make_app(monkeypatch, Failing(responses=[AIMessage(content='unused')]))
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(type(failure)) as caught:
        app.run(app.create_session('A'), [('user', 'run')])
    assert caught.value is failure
    assert [e.event_type for e in turns(events)] == ['turn_started']
    assert not any(e.event_type == 'run_finished' for e in events)


def test_unknown_tool_outcome_is_failed_turn_and_manual_review_remains(monkeypatch):
    failure = BashOutcomeUnknown('PRIVATE_OUTCOME')

    @tool
    def uncertain() -> str:
        """Fail with uncertain side effects."""
        raise failure

    app = make_app(monkeypatch, ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'uncertain', 'args': {}, 'id': 'uncertain'},
    ])]), [uncertain])
    events = []
    app.subscribe_events(events.append)
    session = app.create_session('A')
    with pytest.raises(BashOutcomeUnknown) as caught:
        app.run(session, [('user', 'run')])
    assert caught.value is failure
    assert turns(events, 'turn_finished')[0].data.failure.category == 'tool_outcome_unknown'
    assert events[-1].data.manual_review_required is True
    with pytest.raises(application.SessionManualReviewRequiredError):
        app.run(session, [('user', 'again')])


def test_two_sessions_share_graph_but_never_share_turn_identity(monkeypatch):
    barrier = Barrier(2)

    class Concurrent(ToolModel):
        def _generate(self, *args, **kwargs):
            barrier.wait(timeout=5)
            return super()._generate(*args, **kwargs)

    app = make_app(monkeypatch, Concurrent(responses=[AIMessage(content='done')]))
    sessions = [app.create_session('A') for _ in range(2)]
    events, results = [], []
    app.subscribe_events(events.append)

    def run(session):
        try:
            results.append(app.run(session, [('user', 'run')]))
        except BaseException as error:
            results.append(error)

    workers = [Thread(target=run, args=(session,)) for session in sessions]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=6)
        assert not worker.is_alive()
    assert len(results) == 2 and all(isinstance(r, dict) for r in results), results
    starts = turns(events, 'turn_started')
    assert len({e.data.turn_id for e in starts}) == 2
    for session in sessions:
        scoped = turns([e for e in events if e.session_id == session])
        assert [e.event_type for e in scoped] == ['turn_started', 'turn_finished']
        assert scoped[0].data.turn_id == scoped[1].data.turn_id


def test_legacy_runtime_keeps_existing_six_step_budget_without_app_observation():
    from naiyue_test_agent.agent_runtime import build_runtime_agent

    @tool
    def probe() -> str:
        """Return a synthetic value."""
        return 'value'

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'probe'},
    ]), AIMessage(content='done')])
    graph = build_runtime_agent(model, tools=[probe], system_prompt='', response_format=None,
                                resolve_model=lambda _s: ('A', model), record_selection=lambda *_a: None)
    result = graph.invoke({'messages': [('user', 'run')]}, context={'session_id': 'session'},
                          config={'recursion_limit': 6})
    assert [m.type for m in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert result['messages'][-1].content == 'done'


def test_observer_failure_on_turn_event_does_not_change_business_result(monkeypatch, caplog):
    app = make_app(monkeypatch)
    calls, events = [], []

    def broken(event):
        calls.append(event.event_type)
        if event.event_type == 'turn_started':
            raise ValueError('PRIVATE_OBSERVER')

    app.subscribe_events(broken)
    app.subscribe_events(events.append)
    result = app.run(app.create_session('A'), [('user', 'run')])
    assert calls == ['run_started', 'message_completed', 'turn_started']
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert turns(events, 'turn_finished')[0].data.status == 'completed'
    assert 'event_observer_failed' in caplog.text and 'PRIVATE_' not in caplog.text


def test_turn_reporting_secondary_error_does_not_mask_primary(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime.turn_observation import TurnObserver

    failure = TimeoutError('PRIVATE_PRIMARY')

    class Failing(ToolModel):
        def _generate(self, *args, **kwargs):
            raise failure

    def fail_reporting(self, failure_data):
        raise ValueError('PRIVATE_SECONDARY')

    monkeypatch.setattr(TurnObserver, 'fail', fail_reporting)
    app = make_app(monkeypatch, Failing(responses=[AIMessage(content='unused')]))
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(TimeoutError) as caught:
        app.run(app.create_session('A'), [('user', 'run')])
    assert caught.value is failure and events[-1].data.status == 'failed'
    assert 'turn_failure_publish_failed' in caplog.text and 'PRIVATE_' not in caplog.text


def test_unsupported_state_observation_is_explicit_and_original_custom_result_kept(monkeypatch):
    app = make_app(monkeypatch)
    original_result = {'messages': []}
    monkeypatch.setattr(app._agent, 'invoke', lambda *_a, **_kw: original_result)
    events = []
    app.subscribe_events(events.append)
    assert app.run(app.create_session('A'), [('user', 'run')]) is original_result
    assert [e.event_type for e in turns(events)] == ['turn_observation_unavailable']
    assert turns(events)[0].data.reason == 'state_observation_unavailable'
    assert events[-1].data.status == 'completed'
