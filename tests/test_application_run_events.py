"""Application terminal events follow real graph results and session cleanup."""

import logging
from threading import Barrier, Event, Thread

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.tools import create_session_read_tool
from naiyue_test_agent.agent_runtime.tools.write import WriteOutcomeUnknown
from naiyue_test_agent.model_access import ModelCatalog


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def make_app(monkeypatch, model=None, tools=None):
    model = model or ToolModel(responses=[AIMessage(content='PRIVATE_REPLY')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    return application.build_local_application(
        'synthetic.json', tools=tools or [], system_prompt='', response_format=None,
    )


def run_events_only(handler):
    """Keep Spec 062 assertions focused on the unchanged application lifecycle."""
    def observe(event):
        if event.event_type in ('run_started', 'run_finished'):
            return handler(event)
    return observe


def test_success_events_follow_cleanup_without_changing_result(monkeypatch, caplog):
    app = make_app(monkeypatch)
    session = app.create_session('A')
    received, states = [], []

    def observe(event):
        received.append(event)
        with app._sessions._lock:
            states.append(dict(app._sessions._sessions[session]))

    app.subscribe_events(run_events_only(observe))
    with caplog.at_level(logging.INFO):
        result = app.run(session, [('user', 'PRIVATE_PROMPT')])
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert [(e.event_type, e.sequence) for e in received] == [
        ('run_started', 1), ('run_finished', 8),
    ]
    assert received[0].session_id == received[1].session_id == session
    assert received[0].run_id == received[1].run_id
    assert received[0].data.mode == 'message'
    assert received[1].data.status == 'completed' and received[1].data.failure is None
    assert states[0]['busy'] is True
    assert states[1]['busy'] is False and states[1]['run_id'] is None
    assert states[1]['run_project_instructions'] is None
    assert not states[1]['steering_open'] and not states[1]['stop_requested']
    assert 'PRIVATE_' not in caplog.text + repr(received)
    assert 'thread_id' not in repr(received)
    assert [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')] == [
        'run_started', 'run_completed',
    ]


def test_error_tool_result_can_complete_and_unhandled_error_fails(monkeypatch):
    @tool
    def handled() -> str:
        """Return a recoverable synthetic failure."""
        raise ToolException('handled')

    handled.handle_tool_error = True
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'handled', 'args': {}, 'id': 'call-1'},
    ]), AIMessage(content='recovered')])
    app = make_app(monkeypatch, model, [handled])
    received = []
    app.subscribe_events(run_events_only(received.append))
    result = app.run(app.create_session('A'), [('user', 'run')])
    assert any(isinstance(m, ToolMessage) and m.status == 'error' for m in result['messages'])
    assert received[-1].data.status == 'completed'

    failure = RuntimeError('PRIVATE_TOOL_ERROR')

    @tool
    def unhandled() -> str:
        """Raise a terminal synthetic failure."""
        raise failure

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'unhandled', 'args': {}, 'id': 'call-2'},
    ])])
    app = make_app(monkeypatch, model, [unhandled])
    received = []
    app.subscribe_events(run_events_only(received.append))
    with pytest.raises(RuntimeError) as caught:
        app.run(app.create_session('A'), [('user', 'run')])
    assert caught.value is failure and received[-1].data.status == 'failed'


def test_approval_terminal_is_installed_before_notification_and_resume_is_new_run(
    monkeypatch, tmp_path,
):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('approved', encoding='utf-8')
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read-1'},
    ]), AIMessage(content='done')])
    app = make_app(monkeypatch, model, [create_session_read_tool()])
    session = app.create_workspace_session('A', root)
    received, pending_at_end = [], []

    def observe(event):
        received.append(event)
        if event.event_type == 'run_finished':
            pending_at_end.append(app.pending_tool_approvals(session))

    app.subscribe_events(run_events_only(observe))
    paused = app.run(session, [('user', 'read outside')])
    assert received[-1].data.status == 'interrupted'
    assert pending_at_end[-1] == paused['approval_requests']
    with pytest.raises(application.SessionPendingApprovalError):
        app.run(session, [('user', 'ignore')])
    with pytest.raises(ValueError):
        app.resume_tool_approval(session, [])
    assert len(received) == 2
    result = app.resume_tool_approval(session, ['approve'])
    assert result['messages'][-1].content == 'done'
    assert received[2].data.mode == 'approval_resume'
    assert received[0].run_id != received[2].run_id
    assert [e.sequence for e in received] == [1, 9, 1, 15]
    assert received[-1].data.status == 'completed' and pending_at_end[-1] == []


def test_unknown_outcome_preserves_block_and_original_exception(monkeypatch):
    failure = WriteOutcomeUnknown('PRIVATE_UNKNOWN')

    @tool
    def uncertain() -> str:
        """Raise an uncertain synthetic side effect."""
        raise failure

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'uncertain', 'args': {}, 'id': 'write-1'},
    ])])
    app = make_app(monkeypatch, model, [uncertain])
    session = app.create_session('A')
    received = []
    app.subscribe_events(run_events_only(received.append))
    with pytest.raises(WriteOutcomeUnknown) as caught:
        app.run(session, [('user', 'run')])
    assert caught.value is failure
    assert received[-1].data.status == 'failed'
    assert received[-1].data.manual_review_required is True
    assert received[-1].data.failure.reason == 'tool_outcome_unknown'
    with pytest.raises(application.SessionManualReviewRequiredError):
        app.run(session, [('user', 'retry')])
    assert len(received) == 2


def test_stop_busy_gate_and_active_subscription_snapshot(monkeypatch):
    entered, release = Event(), Event()

    class BlockingModel(ToolModel):
        def _generate(self, *args, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return super()._generate(*args, **kwargs)

    app = make_app(monkeypatch, BlockingModel(responses=[AIMessage(content='answer')]))
    session = app.create_session('A')
    first, second, outcomes = [], [], []
    unsubscribe = app.subscribe_events(run_events_only(first.append))

    def run():
        try:
            outcomes.append(app.run(session, [('user', 'run')]))
        except BaseException as error:
            outcomes.append(error)

    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(timeout=3)
        with pytest.raises(application.SessionBusyError):
            app.run(session, [('user', 'busy')])
        app.subscribe_events(run_events_only(second.append))
        unsubscribe()
        app.request_stop_after_turn(session)
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive() and isinstance(outcomes[0], dict), outcomes
    assert first[-1].data.status == 'stopped'
    assert first[-1].data.stop_mode == 'after_turn'
    assert second == [] and len(first) == 2
    app.run(session, [('user', 'next')])
    assert len(first) == 2 and len(second) == 2


def test_observer_failures_do_not_change_success_or_primary_error(monkeypatch, caplog):
    app = make_app(monkeypatch)
    received = []

    def faulty(_event):
        raise ValueError('PRIVATE_OBSERVER_FAILURE')

    app.subscribe_events(run_events_only(faulty))
    app.subscribe_events(run_events_only(received.append))
    session = app.create_session('A')
    assert app.run(session, [('user', 'run')])['messages'][-1].content == 'PRIVATE_REPLY'
    assert received[-1].data.status == 'completed'
    module = __import__(application.SessionService.__module__, fromlist=['SessionService'])
    failure = RuntimeError('PRIVATE_MODEL_FAILURE')

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(module, 'invoke_agent_run', fail)
    with pytest.raises(RuntimeError) as caught:
        app.run(session, [('user', 'fail')])
    assert caught.value is failure and received[-1].data.status == 'failed'
    assert 'PRIVATE_' not in caplog.text


def test_two_sessions_have_independent_sequences_and_no_fake_start(monkeypatch):
    barrier = Barrier(2)

    class ConcurrentModel(ToolModel):
        def _generate(self, *args, **kwargs):
            barrier.wait(timeout=3)
            return super()._generate(*args, **kwargs)

    app = make_app(monkeypatch, ConcurrentModel(responses=[AIMessage(content='done')]))
    received, outcomes = [], []
    app.subscribe_events(run_events_only(received.append))
    with pytest.raises(KeyError):
        app.run('missing', [('user', 'no session')])
    assert received == []

    def run(session):
        try:
            outcomes.append(app.run(session, [('user', 'run')]))
        except BaseException as error:
            outcomes.append(error)

    sessions = [app.create_session('A') for _ in range(2)]
    workers = [Thread(target=run, args=(session,)) for session in sessions]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=4)
        assert not worker.is_alive()
    assert len(outcomes) == 2 and all(isinstance(r, dict) for r in outcomes), outcomes
    for session in sessions:
        events = [e for e in received if e.session_id == session]
        assert [e.sequence for e in events] == [1, 8]
        assert len({e.run_id for e in events}) == 1


def test_resume_can_interrupt_again_before_final_completion(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('data', encoding='utf-8')
    model = ToolModel(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(outside)}, 'id': 'first'},
        ]),
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(outside)}, 'id': 'second'},
        ]),
        AIMessage(content='done'),
    ])
    app = make_app(monkeypatch, model, [create_session_read_tool()])
    session = app.create_workspace_session('A', root)
    received = []
    app.subscribe_events(run_events_only(received.append))
    app.run(session, [('user', 'read twice')])
    again = app.resume_tool_approval(session, ['approve'])
    assert again['approval_requests'][0]['tool_call_id'] == 'second'
    assert received[-1].data.status == 'interrupted'
    app.resume_tool_approval(session, ['reject'])
    assert [e.data.status for e in received if e.event_type == 'run_finished'] == [
        'interrupted', 'interrupted', 'completed',
    ]
    assert len({e.run_id for e in received}) == 3


def test_old_terminal_uses_cleanup_snapshot_when_new_run_changes_session(monkeypatch):
    app = make_app(monkeypatch)
    session = app.create_session('A')
    received, outcomes = [], []
    app.subscribe_events(run_events_only(received.append))
    module = __import__(application.SessionService.__module__, fromlist=['SessionService'])
    original_pending = module._pending_tool_approval
    original_invoke = module.invoke_agent_run
    cleanup_released, finish_old = Event(), Event()

    def queue_at_finalize(*args, **kwargs):
        pending = original_pending(*args, **kwargs)
        app._sessions._sessions[session]['steering_queue'].append('discarded')
        return pending

    def pause_discard(*_args):
        cleanup_released.set()
        assert finish_old.wait(timeout=3)

    failure = WriteOutcomeUnknown('PRIVATE_NEW_OUTCOME')

    def fail_new(agent, messages, **kwargs):
        if messages[0][1] == 'new':
            raise failure
        return original_invoke(agent, messages, **kwargs)

    monkeypatch.setattr(module, '_pending_tool_approval', queue_at_finalize)
    monkeypatch.setattr(module, 'invoke_agent_run', fail_new)
    monkeypatch.setattr(app._sessions, '_record_steering', pause_discard)

    def old_run():
        try:
            outcomes.append(app.run(session, [('user', 'old')]))
        except BaseException as error:
            outcomes.append(error)

    worker = Thread(target=old_run)
    worker.start()
    try:
        assert cleanup_released.wait(timeout=3)
        with pytest.raises(WriteOutcomeUnknown) as caught:
            app.run(session, [('user', 'new')])
        assert caught.value is failure
    finally:
        finish_old.set()
        worker.join(timeout=3)
    assert not worker.is_alive() and isinstance(outcomes[0], dict), outcomes
    old_id = received[0].run_id
    old_end = next(e for e in received if e.run_id == old_id and e.event_type == 'run_finished')
    assert old_end.data.status == 'completed'
    assert old_end.data.manual_review_required is False
    assert app._sessions._sessions[session]['manual_review_required'] is True
