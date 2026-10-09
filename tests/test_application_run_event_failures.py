"""Run failure observation preserves exceptions, control, and approval state."""

import logging
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.tools import create_session_read_tool
from test_application_run_events import ToolModel, make_app, run_events_only


@pytest.mark.parametrize('stage', ['prepare', 'execute', 'finalize'])
def test_stage_failure_keeps_original_exception_and_cleans(monkeypatch, stage, caplog):
    app = make_app(monkeypatch)
    session = app.create_session('A')
    received = []
    app.subscribe_events(run_events_only(received.append))
    # The current module changes when SessionService is moved; use its owner.
    module = __import__(application.SessionService.__module__, fromlist=['SessionService'])
    function = {'prepare': 'prepare_run_resources', 'execute': 'invoke_agent_run',
                'finalize': '_pending_tool_approval'}[stage]
    failure = RuntimeError('PRIVATE_ORIGINAL_ERROR')

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(module, function, fail)
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError) as caught:
        app.run(session, [('user', 'PRIVATE_PROMPT')])
    assert caught.value is failure
    assert len(received) == 2
    terminal = received[-1].data
    assert terminal.status == 'failed' and terminal.failure.stage == stage
    assert terminal.failure.error_type == 'RuntimeError'
    assert terminal.failure.category == 'unknown'
    assert not app._sessions._sessions[session]['busy']
    assert 'PRIVATE_' not in caplog.text + repr(received)


@pytest.mark.parametrize('has_primary', [False, True])
def test_cleanup_failure_is_reported_without_masking_primary(monkeypatch, has_primary, caplog):
    app = make_app(monkeypatch)
    session = app.create_session('A')
    received = []
    app.subscribe_events(run_events_only(received.append))
    module = __import__(application.SessionService.__module__, fromlist=['SessionService'])
    primary = RuntimeError('PRIVATE_PRIMARY_ERROR')
    secondary = ValueError('PRIVATE_CLEANUP_ERROR')

    def fail_cleanup(*_args):
        raise secondary

    def queue_and_fail(*_args, **_kwargs):
        app._sessions._sessions[session]['steering_queue'].append('PRIVATE_QUEUE')
        raise primary

    original = module._pending_tool_approval

    def queue_and_return(*args, **kwargs):
        app._sessions._sessions[session]['steering_queue'].append('PRIVATE_QUEUE')
        return original(*args, **kwargs)

    monkeypatch.setattr(app._sessions, '_record_steering', fail_cleanup)
    if has_primary:
        monkeypatch.setattr(module, 'prepare_run_resources', queue_and_fail)
    else:
        monkeypatch.setattr(module, '_pending_tool_approval', queue_and_return)
    with pytest.raises(Exception) as caught:
        app.run(session, [('user', 'run')])
    assert caught.value is (primary if has_primary else secondary)
    assert received[-1].data.status == 'failed'
    assert received[-1].data.failure.stage == ('prepare' if has_primary else 'cleanup')
    assert not app._sessions._sessions[session]['busy']
    assert 'PRIVATE_' not in caplog.text


@pytest.mark.parametrize('failure_stage', ['grant', 'execute'])
def test_accepted_approval_failure_is_cleaned_and_keeps_pending(monkeypatch, tmp_path, failure_stage):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('data', encoding='utf-8')
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read-1'},
    ])])
    app = make_app(monkeypatch, model, [create_session_read_tool()])
    session = app.create_workspace_session('A', root)
    received = []
    app.subscribe_events(run_events_only(received.append))
    paused = app.run(session, [('user', 'read')])
    failure = RuntimeError('PRIVATE_RESUME_FAILURE')

    def fail(*_args, **_kwargs):
        raise failure

    if failure_stage == 'grant':
        policy = app._sessions._approval_policies['read']
        app._sessions._approval_policies['read'] = replace(policy, grant_factory=fail)
    else:
        module = __import__(application.SessionService.__module__, fromlist=['SessionService'])
        monkeypatch.setattr(module, 'resume_agent_run_with_resources', fail)
    with pytest.raises(RuntimeError) as caught:
        app.resume_tool_approval(session, ['approve'])
    assert caught.value is failure
    assert len(received) == 4 and received[2].data.mode == 'approval_resume'
    assert received[-1].data.status == 'failed'
    assert received[-1].data.failure.stage == ('prepare' if failure_stage == 'grant' else 'execute')
    assert not app._sessions._sessions[session]['busy']
    assert app.pending_tool_approvals(session) == paused['approval_requests']


@pytest.mark.parametrize('control', [KeyboardInterrupt('PRIVATE_CONTROL')])
def test_process_control_is_not_converted_to_completed_or_failed(monkeypatch, control):
    app = make_app(monkeypatch)
    session = app.create_session('A')
    received = []
    app.subscribe_events(run_events_only(received.append))
    module = __import__(application.SessionService.__module__, fromlist=['SessionService'])

    def stop(*_args, **_kwargs):
        raise control

    monkeypatch.setattr(module, 'invoke_agent_run', stop)
    with pytest.raises(type(control)) as caught:
        app.run(session, [('user', 'run')])
    assert caught.value is control
    assert [e.event_type for e in received] == ['run_started']
    assert not app._sessions._sessions[session]['busy']


@pytest.mark.parametrize('diagnostic_failure', ['raises', 'invalid_codes'])
def test_failure_diagnostic_fault_does_not_replace_primary(monkeypatch, diagnostic_failure, caplog):
    from naiyue_test_agent.application import run_events

    app = make_app(monkeypatch)
    session = app.create_session('A')
    received = []
    app.subscribe_events(run_events_only(received.append))
    module = __import__(application.SessionService.__module__, fromlist=['SessionService'])
    failure = RuntimeError('PRIVATE_ORIGINAL')

    def fail(*_args, **_kwargs):
        raise failure

    def bad_diagnostic(_error):
        if diagnostic_failure == 'raises':
            raise ValueError('PRIVATE_DIAGNOSTIC_ERROR')
        return 'bad category', 'PRIVATE RAW TEXT'

    monkeypatch.setattr(module, 'invoke_agent_run', fail)
    monkeypatch.setattr(run_events, 'classify_model_failure', bad_diagnostic)
    with pytest.raises(RuntimeError) as caught:
        app.run(session, [('user', 'run')])
    assert caught.value is failure
    assert received[-1].data.status == 'failed'
    assert received[-1].data.failure.category == 'unknown'
    assert received[-1].data.failure.error_type == 'RuntimeError'
    assert 'run_failure_diagnostics_unavailable' in caplog.text
    assert 'PRIVATE_' not in caplog.text


def test_terminal_publish_fault_does_not_replace_primary(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher

    app = make_app(monkeypatch)
    session = app.create_session('A')
    module = __import__(application.SessionService.__module__, fromlist=['SessionService'])
    failure = RuntimeError('PRIVATE_ORIGINAL')

    def fail(*_args, **_kwargs):
        raise failure

    original = RunEventPublisher.publish

    def fail_terminal(self, event_type, data):
        if event_type == 'run_finished':
            raise ValueError('PRIVATE_REPORT_ERROR')
        return original(self, event_type, data)

    monkeypatch.setattr(module, 'invoke_agent_run', fail)
    monkeypatch.setattr(RunEventPublisher, 'publish', fail_terminal)
    with pytest.raises(RuntimeError) as caught:
        app.run(session, [('user', 'run')])
    assert caught.value is failure
    assert not app._sessions._sessions[session]['busy']
    assert 'run_event_publish_failed' in caplog.text and 'PRIVATE_' not in caplog.text
