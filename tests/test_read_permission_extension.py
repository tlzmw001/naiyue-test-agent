"""Trusted permission callbacks affect all three Read authorization boundaries."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.agent_runtime.file_permissions import (
    FileAccessDecision, attach_file_permission_policy,
)
from naiyue_test_agent.agent_runtime.tools import create_session_read_tool, create_session_write_tool
from test_application_run_events import make_app, ToolModel


def make_reader(monkeypatch, root, path, decide):
    reader = create_session_read_tool()
    attach_file_permission_policy(reader, decide)
    model = ToolModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'read', 'args': {'path': str(path)}, 'id': 'r'}]),
        AIMessage(content='next turn'),
    ])
    app = make_app(monkeypatch, model, [reader])
    return app, app.create_workspace_session('A', root), reader


def tool_results(result):
    return [m for m in result['messages'] if isinstance(m, ToolMessage)]


def test_deny_inside_file_returns_actionable_error_and_loop_continues(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_CONTENT')
    app, session, _ = make_reader(monkeypatch, tmp_path, path, lambda _req: FileAccessDecision('deny'))
    result = app.run(session, [('user', 'read')])
    assert not result.get('approval_requests')
    assert tool_results(result)[0].status == 'error'
    assert 'file-access policy' in tool_results(result)[0].content
    assert 'PRIVATE_CONTENT' not in tool_results(result)[0].content
    assert result['messages'][-1].content == 'next turn'


def test_allow_outside_file_uses_tool_without_interrupt(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    path = tmp_path / 'outside.txt'
    path.write_text('SYNTHETIC_OUTSIDE')
    received = []
    def allow(request):
        received.append(request)
        return FileAccessDecision('allow')
    app, session, _ = make_reader(monkeypatch, root, path, allow)
    result = app.run(session, [('user', 'read')])
    assert not result.get('approval_requests')
    assert tool_results(result)[0].content == 'SYNTHETIC_OUTSIDE'
    assert all(r.session_id == session and r.target == path.resolve() for r in received)


def test_ask_inside_file_reviews_same_session_and_can_be_approved(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('SYNTHETIC_APPROVED')
    requests = []
    def ask(request):
        requests.append(request)
        assert request.session_id is not None
        return FileAccessDecision('ask')
    app, session, _ = make_reader(monkeypatch, tmp_path, path, ask)
    paused = app.run(session, [('user', 'read')])
    assert paused['approval_requests'][0]['target'] == str(path)
    result = app.resume_tool_approval(session, ['approve'])
    assert tool_results(result)[0].content == 'SYNTHETIC_APPROVED'
    assert all(r.session_id == session for r in requests)


def test_policy_error_returns_safe_tool_feedback(monkeypatch, tmp_path, caplog):
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_CONTENT')
    def fail(_request):
        raise RuntimeError('PRIVATE_POLICY_DETAIL')
    app, session, _ = make_reader(monkeypatch, tmp_path, path, fail)
    result = app.run(session, [('user', 'read')])
    assert tool_results(result)[0].status == 'error'
    assert 'policy is unavailable' in tool_results(result)[0].content
    assert 'PRIVATE_' not in tool_results(result)[0].content + caplog.text


def test_sensitive_path_is_not_force_allowed(monkeypatch, tmp_path):
    path = tmp_path / '.env'
    path.write_text('PRIVATE_CREDENTIAL')
    called = []
    def allow(request):
        called.append(request)
        return FileAccessDecision('allow')
    app, session, _ = make_reader(monkeypatch, tmp_path, path, allow)
    result = app.run(session, [('user', 'read')])
    assert called == [] and tool_results(result)[0].status == 'error'


def test_only_declared_tool_can_attach_policy():
    with pytest.raises(ValueError):
        attach_file_permission_policy(create_session_write_tool(), lambda _r: FileAccessDecision('allow'))


def test_sessions_sharing_one_tool_do_not_share_permission_decision(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('SYNTHETIC_ALLOWED')
    allowed = set()
    reader = create_session_read_tool()
    attach_file_permission_policy(reader, lambda request: FileAccessDecision(
        'allow' if request.session_id in allowed else 'deny'))
    model = ToolModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'read', 'args': {'path': str(path)}, 'id': 'one'}]),
        AIMessage(content='one done'),
        AIMessage(content='', tool_calls=[{'name': 'read', 'args': {'path': str(path)}, 'id': 'two'}]),
        AIMessage(content='two done'),
    ])
    app = make_app(monkeypatch, model, [reader])
    first = app.create_workspace_session('A', tmp_path)
    second = app.create_workspace_session('A', tmp_path)
    allowed.add(first)
    assert tool_results(app.run(first, [('user', 'read')]))[0].content == 'SYNTHETIC_ALLOWED'
    assert tool_results(app.run(second, [('user', 'read')]))[0].status == 'error'


def test_skill_resource_approval_uses_same_context_as_execution(monkeypatch, tmp_path):
    from skill_helpers import write_skill
    path = write_skill(tmp_path, body='SYNTHETIC_SKILL_BODY')
    calls = []
    def ask(request):
        calls.append(request)
        assert request.session_id is not None
        return FileAccessDecision('ask')
    app, session, _ = make_reader(monkeypatch, tmp_path, path, ask)
    paused = app.run(session, [('user', 'read skill')])
    assert paused['approval_requests'][0]['target'] == str(path.resolve())
    result = app.resume_tool_approval(session, ['approve'])
    assert 'SYNTHETIC_SKILL_BODY' in tool_results(result)[0].content
    assert all(r.target == path.resolve() and r.session_id == session for r in calls)


def test_policy_revocation_after_approval_prevents_file_read(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_REVOKED')
    app, session, reader = make_reader(monkeypatch, tmp_path, path, lambda _r: FileAccessDecision('ask'))
    app.run(session, [('user', 'read')])
    attach_file_permission_policy(reader, lambda _r: FileAccessDecision('deny'))
    result = app.resume_tool_approval(session, ['approve'])
    assert tool_results(result)[0].status == 'error'
    assert 'PRIVATE_REVOKED' not in tool_results(result)[0].content


def test_invalid_policy_result_is_not_treated_as_allow(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_CONTENT')
    app, session, _ = make_reader(monkeypatch, tmp_path, path, lambda _r: 'allow')
    result = app.run(session, [('user', 'read')])
    assert tool_results(result)[0].status == 'error'
    assert 'policy is unavailable' in tool_results(result)[0].content


def test_approval_description_does_not_mislabel_inside_file_as_outside():
    from naiyue_test_agent.agent_runtime.tool_approval import APPROVAL_METADATA_KEY
    reader = create_session_read_tool()
    attach_file_permission_policy(reader, lambda _r: FileAccessDecision('ask'))
    assert 'outside' not in reader.metadata[APPROVAL_METADATA_KEY].description


def test_accidental_coroutine_result_is_closed_and_denied(monkeypatch, tmp_path):
    import gc
    import warnings
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_CONTENT')
    async def asynchronous(_r):
        return FileAccessDecision('allow')
    app, session, _ = make_reader(monkeypatch, tmp_path, path, lambda r: asynchronous(r))
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        result = app.run(session, [('user', 'read')])
        gc.collect()
    assert tool_results(result)[0].status == 'error'
    assert not any('never awaited' in str(item.message) for item in captured)


def test_declared_async_and_non_callable_policies_are_rejected():
    reader = create_session_read_tool()
    async def asynchronous(_r):
        return FileAccessDecision('allow')
    for policy in [asynchronous, None, 'allow']:
        with pytest.raises(ValueError):
            attach_file_permission_policy(reader, policy)


def test_contextual_review_registration_is_validated():
    from dataclasses import replace
    from naiyue_test_agent.agent_runtime.tool_approval import (
        APPROVAL_METADATA_KEY, approval_policies_for_tools,
    )
    reader = create_session_read_tool()
    reader.metadata[APPROVAL_METADATA_KEY] = replace(
        reader.metadata[APPROVAL_METADATA_KEY], review_with_context=None)
    with pytest.raises(ValueError):
        approval_policies_for_tools([reader])


def test_rejecting_inside_approval_does_not_read_file(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_REJECTED')
    app, session, _ = make_reader(monkeypatch, tmp_path, path, lambda _r: FileAccessDecision('ask'))
    app.run(session, [('user', 'read')])
    result = app.resume_tool_approval(session, ['reject'])
    assert tool_results(result)[0].status == 'error'
    assert 'PRIVATE_REJECTED' not in tool_results(result)[0].content
    assert result['messages'][-1].content == 'next turn'


def test_control_exception_is_not_converted_to_permission_denial(monkeypatch, tmp_path):
    from langgraph.errors import GraphBubbleUp
    from naiyue_test_agent.agent_runtime.file_permissions import FileAccessRequest, decide_file_access
    reader = create_session_read_tool()
    def signal(_r):
        raise GraphBubbleUp('synthetic control')
    attach_file_permission_policy(reader, signal)
    with pytest.raises(GraphBubbleUp):
        decide_file_access(reader, FileAccessRequest('read', 'read', tmp_path, tmp_path / 'note'), 'allow')


def test_permission_callback_is_not_in_model_schema():
    reader = create_session_read_tool()
    attach_file_permission_policy(reader, lambda _r: FileAccessDecision('allow'))
    assert set(reader.tool_call_schema.model_fields) == {'path', 'offset', 'limit'}
