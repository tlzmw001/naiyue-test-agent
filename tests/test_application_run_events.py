"""Application terminal events follow real graph results and session cleanup."""

import asyncio

from native_application_helpers import async_test, build_application

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import logging
from threading import Barrier, Event

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent import application
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.tools.processes import ProcessCleanupError
from naiyue_test_agent.model_access import ModelCatalog


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        # The async fixture transports the original error object. The stdlib
        # executor otherwise reconstructs builtin TimeoutError instances.
        def generate():
            try:
                return self._generate(messages, stop=stop, run_manager=None, **kwargs), None
            except BaseException as error:
                return None, error
        result, error = await asyncio.to_thread(generate)
        if error is not None:
            raise error
        return result


async def make_app(monkeypatch, model=None, tools=None):
    model = model or ToolModel(responses=[AIMessage(content='PRIVATE_REPLY')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    return await build_application(
        'synthetic.json', tools=tools or [], system_prompt='', response_format=None,
    )


def run_events_only(handler):
    """Keep Spec 062 assertions focused on the unchanged application lifecycle."""
    def observe(event):
        if event.event_type in ('run_started', 'run_finished'):
            return handler(event)
    return observe


@async_test
async def test_success_events_follow_cleanup_without_changing_result(monkeypatch, caplog):
    app = await make_app(monkeypatch)
    session = await app.acreate_session('A')
    received, states = [], []

    def observe(event):
        received.append(event)
        with app._sessions._lock:
            states.append(dict(app._sessions._sessions[session]))

    app.subscribe_events(run_events_only(observe))
    with caplog.at_level(logging.INFO):
        result = await app.arun(session, [('user', 'PRIVATE_PROMPT')])
    assert result['messages'][-1].content == 'PRIVATE_REPLY'
    assert [(e.event_type, e.sequence) for e in received] == [
        ('run_started', 1), ('run_finished', 11),
    ]
    assert received[0].session_id == received[1].session_id == session
    assert received[0].run_id == received[1].run_id
    assert received[0].data.mode == 'message'
    assert received[1].data.status == 'completed' and received[1].data.failure is None
    assert states[0]['busy'] is True
    assert states[1]['busy'] is False and states[1]['run_id'] is None
    assert states[1]['run_prompt_parts'] == []
    assert not states[1]['steering_open'] and not states[1]['stop_requested']
    assert 'PRIVATE_' not in caplog.text + repr(received)
    assert 'thread_id' not in repr(received)
    assert [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')] == [
        'run_started', 'run_completed',
    ]


@async_test
async def test_explicit_and_unexpected_tool_errors_allow_run_completion(monkeypatch):
    @tool
    def handled() -> str:
        """Return a recoverable synthetic failure."""
        raise ToolException('handled')

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'handled', 'args': {}, 'id': 'call-1'},
    ]), AIMessage(content='recovered')])
    app = await make_app(monkeypatch, model, [ToolSpec(handled, handled.args_schema, permissions=ToolPermission(default='allow'))])
    received = []
    app.subscribe_events(run_events_only(received.append))
    result = await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert any(isinstance(m, ToolMessage) and m.status == 'error' for m in result['messages'])
    assert received[-1].data.status == 'completed'

    failure = RuntimeError('PRIVATE_TOOL_ERROR')

    @tool
    def unhandled() -> str:
        """Raise a terminal synthetic failure."""
        raise failure

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'unhandled', 'args': {}, 'id': 'call-2'},
    ]), AIMessage(content='handled unexpected failure')])
    app = await make_app(monkeypatch, model, [ToolSpec(unhandled, unhandled.args_schema, permissions=ToolPermission(default='allow'))])
    received = []
    app.subscribe_events(run_events_only(received.append))
    result = await app.arun(await app.acreate_session('A'), [('user', 'run')])
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert feedback.status == 'error' and feedback.tool_call_id == 'call-2'
    assert 'RuntimeError' in feedback.content and 'PRIVATE_TOOL_ERROR' not in feedback.content
    assert result['messages'][-1].content == 'handled unexpected failure'
    assert received[-1].data.status == 'completed' and received[-1].data.failure is None


@async_test
async def test_approval_terminal_is_installed_before_notification_and_resume_is_new_run(
    monkeypatch, tmp_path,
):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('approved', encoding='utf-8')
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read-1'},
    ]), AIMessage(content='done')])
    app = await make_app(monkeypatch, model, [create_session_read_tool()])
    session = await app.acreate_workspace_session('A', root)
    received, pending_at_end = [], []

    def observe(event):
        received.append(event)
        if event.event_type == 'run_finished':
            pending_at_end.append(app.pending_tool_approvals(session))

    app.subscribe_events(run_events_only(observe))
    paused = await app.arun(session, [('user', 'read outside')])
    assert received[-1].data.status == 'interrupted'
    assert pending_at_end[-1] == paused['approval_requests']
    with pytest.raises(application.SessionPendingApprovalError):
        await app.arun(session, [('user', 'ignore')])
    with pytest.raises(ValueError):
        await app.aresume_tool_approval(session, [])
    assert len(received) == 2
    result = await app.aresume_tool_approval(session, ['approve'])
    assert result['messages'][-1].content == 'done'
    assert received[2].data.mode == 'approval_resume'
    assert received[0].run_id != received[2].run_id
    # Native streaming adds one message_delta for the final reply.
    # Resume adds queued/preparing and Read's detection/read/last-count reports.
    assert [e.sequence for e in received] == [1, 11, 1, 23]
    assert received[-1].data.status == 'completed' and pending_at_end[-1] == []


@async_test
async def test_unknown_outcome_preserves_block_and_original_exception(monkeypatch):
    failure = ProcessCleanupError('PRIVATE_UNKNOWN')

    @tool
    def uncertain() -> str:
        """Raise an uncertain synthetic side effect."""
        raise failure

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'uncertain', 'args': {}, 'id': 'write-1'},
    ])])
    app = await make_app(monkeypatch, model, [ToolSpec(uncertain, uncertain.args_schema, permissions=ToolPermission(default='allow'))])
    session = await app.acreate_session('A')
    received = []
    app.subscribe_events(run_events_only(received.append))
    with pytest.raises(ProcessCleanupError) as caught:
        await app.arun(session, [('user', 'run')])
    assert caught.value is failure
    assert received[-1].data.status == 'failed'
    assert received[-1].data.manual_review_required is True
    assert received[-1].data.failure.reason == 'tool_outcome_unknown'
    with pytest.raises(application.SessionManualReviewRequiredError):
        await app.arun(session, [('user', 'retry')])
    assert len(received) == 2


@async_test
async def test_stop_busy_gate_and_active_subscription_snapshot(monkeypatch):
    entered, release = Event(), Event()

    class BlockingModel(ToolModel):
        def _generate(self, *args, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return super()._generate(*args, **kwargs)

    app = await make_app(monkeypatch, BlockingModel(responses=[AIMessage(content='answer')]))
    session = await app.acreate_session('A')
    first, second, outcomes = [], [], []
    unsubscribe = app.subscribe_events(run_events_only(first.append))

    async def run():
        try:
            outcomes.append(await app.arun(session, [('user', 'run')]))
        except BaseException as error:
            outcomes.append(error)

    worker = asyncio.create_task(run())

    try:
        assert await asyncio.to_thread(entered.wait, timeout=3)
        with pytest.raises(application.SessionBusyError):
            await app.arun(session, [('user', 'busy')])
        app.subscribe_events(run_events_only(second.append))
        unsubscribe()
        app.request_stop(session)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(worker), 3)
    assert worker.done() and isinstance(outcomes[0], dict), outcomes
    assert first[-1].data.status == 'stopped'
    assert first[-1].data.stop_mode == 'active'
    assert second == [] and len(first) == 2
    await app.arun(session, [('user', 'next')])
    assert len(first) == 2 and len(second) == 2


@async_test
async def test_observer_failures_do_not_change_success_or_primary_error(monkeypatch, caplog):
    app = await make_app(monkeypatch)
    received = []

    def faulty(_event):
        raise ValueError('PRIVATE_OBSERVER_FAILURE')

    app.subscribe_events(run_events_only(faulty))
    app.subscribe_events(run_events_only(received.append))
    session = await app.acreate_session('A')
    assert (await app.arun(session, [('user', 'run')]))['messages'][-1].content == 'PRIVATE_REPLY'
    assert received[-1].data.status == 'completed'
    module = __import__(application.SessionService.__module__, fromlist=['SessionService'])
    failure = RuntimeError('PRIVATE_MODEL_FAILURE')

    async def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(module, 'ainvoke_agent_run', fail)
    with pytest.raises(RuntimeError) as caught:
        await app.arun(session, [('user', 'fail')])
    assert caught.value is failure and received[-1].data.status == 'failed'
    assert 'PRIVATE_' not in caplog.text


@async_test
async def test_two_sessions_have_independent_sequences_and_no_fake_start(monkeypatch):
    barrier = Barrier(2)

    class ConcurrentModel(ToolModel):
        def _generate(self, *args, **kwargs):
            barrier.wait(timeout=3)
            return super()._generate(*args, **kwargs)

    app = await make_app(monkeypatch, ConcurrentModel(responses=[AIMessage(content='done')]))
    received, outcomes = [], []
    app.subscribe_events(run_events_only(received.append))
    with pytest.raises(KeyError):
        await app.arun('missing', [('user', 'no session')])
    assert received == []

    async def run(session):
        try:
            outcomes.append(await app.arun(session, [('user', 'run')]))
        except BaseException as error:
            outcomes.append(error)

    sessions = [await app.acreate_session('A') for _ in range(2)]
    workers = [asyncio.create_task(run(session)) for session in sessions]
    for worker in workers:
        await asyncio.wait_for(asyncio.shield(worker), 4)
        assert worker.done()
    assert len(outcomes) == 2 and all(isinstance(r, dict) for r in outcomes), outcomes
    for session in sessions:
        events = [e for e in received if e.session_id == session]
        assert [e.sequence for e in events] == [1, 11]
        assert len({e.run_id for e in events}) == 1


@async_test
async def test_resume_can_interrupt_again_before_final_completion(monkeypatch, tmp_path):
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
    app = await make_app(monkeypatch, model, [create_session_read_tool()])
    session = await app.acreate_workspace_session('A', root)
    received = []
    app.subscribe_events(run_events_only(received.append))
    await app.arun(session, [('user', 'read twice')])
    again = await app.aresume_tool_approval(session, ['approve'])
    assert again['approval_requests'][0]['tool_call_id'] == 'second'
    assert received[-1].data.status == 'interrupted'
    await app.aresume_tool_approval(session, ['reject'])
    assert [e.data.status for e in received if e.event_type == 'run_finished'] == [
        'interrupted', 'interrupted', 'completed',
    ]
    assert len({e.run_id for e in received}) == 3


@async_test
async def test_terminal_uses_cleanup_snapshot_after_session_state_changes(monkeypatch):
    # _cleanup_run is synchronous on the owner loop. Test its snapshot directly:
    # a later state change cannot retroactively alter the old terminal payload.
    from naiyue_test_agent.application.run_events import RunObservation
    app = await make_app(monkeypatch)
    sid = await app.acreate_session('A')
    session = app._sessions._sessions[sid]
    received = []
    app.subscribe_events(run_events_only(received.append))
    observation = RunObservation(app._sessions._events.publisher(sid, 'old-run'), 'message')
    observation.start()
    observation.result({'messages': []}, None)
    session['busy'], session['run_id'] = True, 'old-run'
    session['steering_queue'].append('discarded')
    review = app._sessions._cleanup_run(sid, 'old-run', session, observation)
    assert review is False and not session['busy'] and session['run_id'] is None
    session['manual_review_required'], session['run_id'] = True, 'later-run'
    observation.finish(review)
    assert received[-1].run_id == 'old-run'
    assert received[-1].data.status == 'completed'
    assert received[-1].data.manual_review_required is False
    assert session['manual_review_required'] is True and session['run_id'] == 'later-run'
