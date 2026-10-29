"""Run failure observation preserves exceptions, control, and approval state."""

from native_application_helpers import async_test

import logging

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.tools import create_session_read_tool
from test_application_run_events import ToolModel, make_app, run_events_only


@pytest.mark.parametrize('stage', ['prepare', 'execute', 'finalize'])
@async_test
async def test_stage_failure_keeps_original_exception_and_cleans(monkeypatch, stage, caplog):
    app = await make_app(monkeypatch)
    session = await app.host.sessions.create('A')
    received = []
    app.host.observability.subscribe(run_events_only(received.append))
    # Inject at the session execution owner, where the graph call is made.
    module = __import__('naiyue_test_agent.application.sessions.session_execution', fromlist=['RunLifecycle'])
    function = {'prepare': 'prepare_resources', 'execute': 'ainvoke_agent_run',
                'finalize': 'pending_tool_approval'}[stage]
    failure = RuntimeError('PRIVATE_ORIGINAL_ERROR')

    def fail(*_args, **_kwargs):
        raise failure

    async def fail_async(*args, **kwargs):
        return fail(*args, **kwargs)
    monkeypatch.setattr(module, function, fail_async if stage == 'execute' else fail)
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError) as caught:
        await app.host.runs.start(session, [('user', 'PRIVATE_PROMPT')])
    assert caught.value is failure
    assert len(received) == 2
    terminal = received[-1].data
    assert terminal.status == 'failed' and terminal.failure.stage == stage
    assert terminal.failure.error_type == 'RuntimeError'
    assert terminal.failure.category == 'unknown'
    assert not app.host.sessions._sessions.require(session).describe_session()['busy']
    assert 'PRIVATE_' not in caplog.text + repr(received)


@pytest.mark.parametrize('has_primary', [False, True])
@async_test
async def test_cleanup_failure_is_reported_without_masking_primary(monkeypatch, has_primary, caplog):
    app = await make_app(monkeypatch)
    session = await app.host.sessions.create('A')
    received = []
    app.host.observability.subscribe(run_events_only(received.append))
    module = __import__('naiyue_test_agent.application.sessions.session_execution', fromlist=['RunLifecycle'])
    primary = RuntimeError('PRIVATE_PRIMARY_ERROR')
    secondary = ValueError('PRIVATE_CLEANUP_ERROR')

    original = app.host.sessions._sessions._sessions[session].lifecycle._cleanup_run

    def fail_cleanup(*args, **kwargs):
        # Fail after real cleanup, rather than mutating a closed control queue.
        original(*args, **kwargs)
        raise secondary

    def fail_prepare(*_args, **_kwargs):
        raise primary

    monkeypatch.setattr(app.host.sessions._sessions._sessions[session].lifecycle, '_cleanup_run', fail_cleanup)
    if has_primary:
        monkeypatch.setattr(module, 'prepare_resources', fail_prepare)
    with pytest.raises(Exception) as caught:
        await app.host.runs.start(session, [('user', 'run')])
    assert caught.value is (primary if has_primary else secondary)
    assert received[-1].data.status == 'failed'
    assert received[-1].data.failure.stage == ('prepare' if has_primary else 'cleanup')
    assert not app.host.sessions._sessions.require(session).describe_session()['busy']
    assert 'PRIVATE_' not in caplog.text


@pytest.mark.parametrize('failure_stage', ['grant', 'execute'])
@async_test
async def test_accepted_approval_failure_is_cleaned_and_keeps_pending(monkeypatch, tmp_path, failure_stage):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('data', encoding='utf-8')
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read-1'},
    ])])
    app = await make_app(monkeypatch, model, [create_session_read_tool()])
    session = await app.host.sessions.create_workspace('A', root)
    received = []
    app.host.observability.subscribe(run_events_only(received.append))
    paused = await app.host.runs.start(session, [('user', 'read')])
    failure = RuntimeError('PRIVATE_RESUME_FAILURE')

    def fail(*_args, **_kwargs):
        raise failure

    if failure_stage == 'grant':
        from naiyue_test_agent.application.sessions import session_approval
        monkeypatch.setattr(session_approval, 'OneUseApprovalGrants', fail)
    else:
        module = __import__('naiyue_test_agent.application.sessions.session_execution', fromlist=['RunLifecycle'])
        async def fail_async(*args, **kwargs):
            return fail(*args, **kwargs)
        monkeypatch.setattr(module, 'aresume_agent_run_with_resources', fail_async)
    with pytest.raises(RuntimeError) as caught:
        await app.host.runs.resume_approval(session, ['approve'])
    assert caught.value is failure
    assert len(received) == 4 and received[2].data.mode == 'approval_resume'
    assert received[-1].data.status == 'failed'
    assert received[-1].data.failure.stage == ('prepare' if failure_stage == 'grant' else 'execute')
    assert not app.host.sessions._sessions.require(session).describe_session()['busy']
    assert app.host.runs.pending_tools(session) == paused['approval_requests']


@pytest.mark.parametrize('control', [KeyboardInterrupt('PRIVATE_CONTROL')])
@async_test
async def test_process_control_is_not_converted_to_completed_or_failed(monkeypatch, control):
    app = await make_app(monkeypatch)
    session = await app.host.sessions.create('A')
    received = []
    app.host.observability.subscribe(run_events_only(received.append))
    module = __import__('naiyue_test_agent.application.sessions.session_execution', fromlist=['RunLifecycle'])

    async def stop(*_args, **_kwargs):
        raise control

    monkeypatch.setattr(module, 'ainvoke_agent_run', stop)
    with pytest.raises(type(control)) as caught:
        await app.host.runs.start(session, [('user', 'run')])
    assert caught.value is control
    assert [e.event_type for e in received] == ['run_started']
    assert not app.host.sessions._sessions.require(session).describe_session()['busy']


@pytest.mark.parametrize('diagnostic_failure', ['raises', 'invalid_codes'])
@async_test
async def test_failure_diagnostic_fault_does_not_replace_primary(monkeypatch, diagnostic_failure, caplog):
    from naiyue_test_agent.application.observability import run_events

    app = await make_app(monkeypatch)
    session = await app.host.sessions.create('A')
    received = []
    app.host.observability.subscribe(run_events_only(received.append))
    module = __import__('naiyue_test_agent.application.sessions.session_execution', fromlist=['RunLifecycle'])
    failure = RuntimeError('PRIVATE_ORIGINAL')

    def fail(*_args, **_kwargs):
        raise failure

    def bad_diagnostic(_self, _error):
        if diagnostic_failure == 'raises':
            raise ValueError('PRIVATE_DIAGNOSTIC_ERROR')
        from types import SimpleNamespace
        return SimpleNamespace(category='bad category', reason='PRIVATE RAW TEXT', error_type='RuntimeError')

    async def execute_failure(*args, **kwargs):
        return fail(*args, **kwargs)
    monkeypatch.setattr(module, 'ainvoke_agent_run', execute_failure)
    monkeypatch.setattr(run_events.RunObservation, '_describe_failure', bad_diagnostic)
    with pytest.raises(RuntimeError) as caught:
        await app.host.runs.start(session, [('user', 'run')])
    assert caught.value is failure
    assert received[-1].data.status == 'failed'
    assert received[-1].data.failure.category == 'unknown'
    assert received[-1].data.failure.error_type == 'RuntimeError'
    assert 'run_failure_diagnostics_unavailable' in caplog.text
    assert 'PRIVATE_' not in caplog.text


@async_test
async def test_terminal_publish_fault_does_not_replace_primary(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher

    app = await make_app(monkeypatch)
    session = await app.host.sessions.create('A')
    module = __import__('naiyue_test_agent.application.sessions.session_execution', fromlist=['RunLifecycle'])
    failure = RuntimeError('PRIVATE_ORIGINAL')

    def fail(*_args, **_kwargs):
        raise failure

    original = RunEventPublisher.publish

    def fail_terminal(self, event_type, data):
        if event_type == 'run_finished':
            raise ValueError('PRIVATE_REPORT_ERROR')
        return original(self, event_type, data)

    async def execute_failure(*args, **kwargs):
        return fail(*args, **kwargs)
    monkeypatch.setattr(module, 'ainvoke_agent_run', execute_failure)
    monkeypatch.setattr(RunEventPublisher, 'publish', fail_terminal)
    with pytest.raises(RuntimeError) as caught:
        await app.host.runs.start(session, [('user', 'run')])
    assert caught.value is failure
    assert not app.host.sessions._sessions.require(session).describe_session()['busy']
    assert 'run_event_publish_failed' in caplog.text and 'PRIVATE_' not in caplog.text
