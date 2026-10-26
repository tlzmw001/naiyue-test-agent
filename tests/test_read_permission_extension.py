"""Trusted permission callbacks affect all three Read authorization boundaries."""

from native_application_helpers import async_test, build_application

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from pathlib import Path
from dataclasses import replace
from naiyue_test_agent.tools.permissions import FilePermission
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.settings import AppSettings
from test_application_run_events import ToolModel
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog


async def make_reader(monkeypatch, root, path, decide, **rules):
    class CallbackPermission(FilePermission):
        def default_decision(self, subject):
            return decide(subject)
    reader = replace(create_session_read_tool(), permissions=CallbackPermission('path', 'read'))
    model = ToolModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'read', 'args': {'path': str(path)}, 'id': 'r'}]),
        AIMessage(content='next turn'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('synthetic', tools=[reader], system_prompt='', response_format=None,
        settings=AppSettings.model_validate({'permissions': rules}))
    return app, await app.host.sessions.create_workspace('A', root), reader


def tool_results(result):
    return [m for m in result['messages'] if isinstance(m, ToolMessage)]


@async_test
async def test_deny_inside_file_returns_actionable_error_and_loop_continues(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_CONTENT')
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, lambda _req: 'allow', deny=[{'tool': 'read', 'path': str(path)}])
    result = await app.host.runs.start(session, [('user', 'read')])
    assert not result.get('approval_requests')
    assert tool_results(result)[0].status == 'error'
    assert 'permission policy' in tool_results(result)[0].content
    assert 'PRIVATE_CONTENT' not in tool_results(result)[0].content
    assert result['messages'][-1].content == 'next turn'


@async_test
async def test_allow_outside_file_uses_tool_without_interrupt(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    path = tmp_path / 'outside.txt'
    path.write_text('SYNTHETIC_OUTSIDE')
    received = []
    def allow(request):
        received.append(request)
        return 'allow'
    app, session, _ = await make_reader(monkeypatch, root, path, allow)
    result = await app.host.runs.start(session, [('user', 'read')])
    assert not result.get('approval_requests')
    assert tool_results(result)[0].content == 'SYNTHETIC_OUTSIDE'
    assert all(r['session_id'] == session and Path(r['resolved']) == path.resolve() for r in received)


@async_test
async def test_ask_inside_file_reviews_same_session_and_can_be_approved(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('SYNTHETIC_APPROVED')
    requests = []
    def ask(request):
        requests.append(request)
        assert request['session_id'] is not None
        return 'ask'
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, ask)
    paused = await app.host.runs.start(session, [('user', 'read')])
    assert paused['approval_requests'][0]['target'] == str(path)
    result = await app.host.runs.resume_approval(session, ['approve'])
    assert tool_results(result)[0].content == 'SYNTHETIC_APPROVED'
    assert all(r['session_id'] == session for r in requests)


@async_test
async def test_policy_error_returns_safe_tool_feedback(monkeypatch, tmp_path, caplog):
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_CONTENT')
    def fail(_request):
        raise RuntimeError('PRIVATE_POLICY_DETAIL')
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, fail)
    paused = await app.host.runs.start(session, [('user', 'read')])
    assert paused['approval_requests'] and not tool_results(paused)
    result = await app.host.runs.resume_approval(session, ['reject'])
    assert tool_results(result)[0].status == 'error'
    assert 'PRIVATE_' not in tool_results(result)[0].content + caplog.text


@async_test
async def test_sensitive_path_requires_review_without_explicit_allow(monkeypatch, tmp_path):
    path = tmp_path / '.env'
    path.write_text('PRIVATE_CREDENTIAL')
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, lambda _: 'ask')
    paused = await app.host.runs.start(session, [('user', 'read')])
    assert paused['approval_requests'] and not tool_results(paused)
    result = await app.host.runs.resume_approval(session, ['reject'])
    assert tool_results(result)[0].status == 'error' and 'PRIVATE_CREDENTIAL' not in str(tool_results(result))


@async_test
async def test_sensitive_file_can_be_explicitly_allowed(monkeypatch, tmp_path):
    path = tmp_path / '.env'
    path.write_text('synthetic explicitly allowed')
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, lambda _: 'ask', allow=[{'tool': 'read', 'path': str(path)}])
    result = await app.host.runs.start(session, [('user', 'read')])
    assert tool_results(result)[0].content == 'synthetic explicitly allowed'


@async_test
async def test_sessions_sharing_one_tool_do_not_share_permission_decision(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('SYNTHETIC_ALLOWED')
    allowed = set()
    def decide(subject):
        return 'allow' if subject['session_id'] in allowed else 'ask'
    app, first, reader = await make_reader(monkeypatch, tmp_path, path, decide, mode='NoAsk')
    allowed.add(first)
    assert tool_results(await app.host.runs.start(first, [('user', 'read')]))[0].content == 'SYNTHETIC_ALLOWED'
    # A second application run uses the same original tool but a different session.
    second = await app.host.sessions.create_workspace('A', tmp_path)
    assert tool_results(await app.host.runs.start(second, [('user', 'read')]))[0].status == 'error'


@async_test
async def test_skill_resource_approval_uses_same_context_as_execution(monkeypatch, tmp_path):
    from skill_helpers import write_skill
    path = write_skill(tmp_path, body='SYNTHETIC_SKILL_BODY')
    calls = []
    def ask(request):
        calls.append(request)
        assert request['session_id'] is not None
        return 'ask'
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, ask)
    paused = await app.host.runs.start(session, [('user', 'read skill')])
    assert paused['approval_requests'][0]['target'] == str(path.resolve())
    result = await app.host.runs.resume_approval(session, ['approve'])
    assert 'SYNTHETIC_SKILL_BODY' in tool_results(result)[0].content
    assert all(Path(r['resolved']) == path.resolve() and r['session_id'] == session for r in calls)


@async_test
async def test_default_callback_change_does_not_recheck_a_saved_approval(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('approved content')
    decisions = ['ask']
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, lambda _: decisions[0])
    await app.host.runs.start(session, [('user', 'read')])
    decisions[0] = 'allow'
    result = await app.host.runs.resume_approval(session, ['approve'])
    assert tool_results(result)[0].content == 'approved content'


@async_test
async def test_invalid_policy_result_is_not_treated_as_allow(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_CONTENT')
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, lambda _: {'decision': 'allow'})
    result = await app.host.runs.start(session, [('user', 'read')])
    assert result['approval_requests'] and not tool_results(result)


@async_test
async def test_accidental_coroutine_result_is_closed_and_requires_review(monkeypatch, tmp_path):
    import gc
    import warnings
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_CONTENT')
    async def asynchronous(_):
        return 'allow'
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, asynchronous)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        result = await app.host.runs.start(session, [('user', 'read')])
        gc.collect()
    assert result['approval_requests'] and not tool_results(result)
    assert not any('never awaited' in str(item.message) for item in captured)


def test_declared_async_and_non_callable_policies_are_rejected():
    from naiyue_test_agent.application import prepare_tool_registry
    class Asynchronous(FilePermission):
        async def default_decision(self, subject):
            return 'allow'
    for permission in [Asynchronous(), None, 'allow']:
        with pytest.raises(ValueError):
            prepare_tool_registry([replace(create_session_read_tool(), permissions=permission)])


def test_file_permission_path_field_registration_is_validated():
    from naiyue_test_agent.application import prepare_tool_registry
    with pytest.raises(ValueError):
        prepare_tool_registry([replace(create_session_read_tool(), permissions=FilePermission('missing'))])


@async_test
async def test_rejecting_inside_approval_does_not_read_file(monkeypatch, tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('PRIVATE_REJECTED')
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, lambda _r: 'ask')
    await app.host.runs.start(session, [('user', 'read')])
    result = await app.host.runs.resume_approval(session, ['reject'])
    assert tool_results(result)[0].status == 'error'
    assert 'PRIVATE_REJECTED' not in tool_results(result)[0].content
    assert result['messages'][-1].content == 'next turn'


@async_test
async def test_control_exception_is_not_converted_to_permission_denial(monkeypatch, tmp_path):
    from langgraph.errors import GraphBubbleUp
    path = tmp_path / 'note.txt'
    path.write_text('content')
    def signal(_):
        raise GraphBubbleUp('synthetic control')
    app, session, _ = await make_reader(monkeypatch, tmp_path, path, signal)
    with pytest.raises(GraphBubbleUp):
        await app.host.runs.start(session, [('user', 'read')])


def test_permission_callback_is_not_in_model_schema():
    reader = create_session_read_tool()
    assert set(reader.tool.tool_call_schema.model_fields) == {'path', 'offset', 'limit'}
