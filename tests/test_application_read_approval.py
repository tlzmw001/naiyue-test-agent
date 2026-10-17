"""Read approval pauses before IO and binds each approval to one target."""

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.tools import ToolSettings
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def make_app(monkeypatch, responses, *, permissions=None):
    model = ToolCapableFake(responses=responses)
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    return application.build_local_application(
        Path('synthetic.json'), tools=[create_session_read_tool()],
        system_prompt='', response_format=None, settings=ToolSettings.model_validate({'permissions': permissions or {}}),
    )


def tool_call(path, call_id='call-1'):
    return {'name': 'read', 'args': {'path': str(path)}, 'id': call_id}


def tool_messages(result):
    return [message for message in result['messages'] if isinstance(message, ToolMessage)]


def test_external_read_pauses_then_approve_reads_once(monkeypatch, tmp_path, caplog):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('APPROVED_CONTENT', encoding='utf-8')
    app = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[tool_call(outside)]),
        AIMessage(content='approved answer'),
    ])
    session = app.create_workspace_session('A', root)

    with caplog.at_level('INFO'):
        pending = app.run(session, [('user', 'read external')])

    assert len(pending['approval_requests']) == 1
    assert pending['approval_requests'][0]['target'] == str(outside.resolve())
    assert tool_messages(pending) == []
    assert app.pending_tool_approvals(session) == pending['approval_requests']
    with pytest.raises(application.SessionPendingApprovalError):
        app.run(session, [('user', 'skip approval')])
    with pytest.raises(ValueError):
        app.resume_tool_approval(session, [])
    assert app.pending_tool_approvals(session) == pending['approval_requests']

    with caplog.at_level('INFO'):
        result = app.resume_tool_approval(session, ['approve'])

    assert app.pending_tool_approvals(session) == []
    assert [message.content for message in tool_messages(result)] == ['APPROVED_CONTENT']
    assert result['messages'][-1].content == 'approved answer'
    assert result['resume_start_index'] == len(pending['messages'])
    events = [r.run_event['event'] for r in caplog.records if hasattr(r, 'run_event')]
    assert events == ['run_started', 'run_interrupted', 'run_started', 'run_completed']
    approval_events = [r.approval_event for r in caplog.records
                       if hasattr(r, 'approval_event')]
    assert [item['event'] for item in approval_events] == [
        'approval_requested', 'approval_review_submitted', 'approval_resumed',
    ]
    assert approval_events[1]['approved_count'] == 1
    assert approval_events[1]['rejected_count'] == 0
    assert str(outside) not in caplog.text
    assert 'APPROVED_CONTENT' not in caplog.text


def test_reject_skips_read_and_sensitive_external_path_is_denied(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('PRIVATE_CONTENT', encoding='utf-8')
    secret = tmp_path / '.env'
    secret.write_text('SECRET', encoding='utf-8')
    app = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[tool_call(outside, 'rejected')]),
        AIMessage(content='read rejected'),
        AIMessage(content='', tool_calls=[tool_call(secret, 'secret')]),
        AIMessage(content='secret denied'),
    ])
    session = app.create_workspace_session('A', root)

    pending = app.run(session, [('user', 'try outside')])
    assert len(pending['approval_requests']) == 1
    rejected = app.resume_tool_approval(session, ['reject'])
    assert tool_messages(rejected)[0].status == 'error'
    assert 'PRIVATE_CONTENT' not in str(rejected)

    app.set_permission_mode(session, 'NoAsk')
    denied = app.run(session, [('user', 'try secret')])
    assert 'approval_requests' not in denied
    assert tool_messages(denied)[-1].status == 'error'
    assert 'SECRET' not in str(denied)


def test_changed_symlink_target_cannot_use_old_approval(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    first = tmp_path / 'first.md'
    second = tmp_path / 'second.md'
    first.write_text('FIRST_PRIVATE', encoding='utf-8')
    second.write_text('SECOND_PRIVATE', encoding='utf-8')
    link = root / 'external-link.md'
    link.symlink_to(first)
    app = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[tool_call(link)]),
        AIMessage(content='target changed'),
    ])
    session = app.create_workspace_session('A', root)

    pending = app.run(session, [('user', 'read link')])
    assert pending['approval_requests'][0]['target'] == str(first.resolve())
    link.unlink()
    link.symlink_to(second)
    result = app.resume_tool_approval(session, ['approve'])

    assert tool_messages(result)[0].status == 'error'
    assert 'changed' in tool_messages(result)[0].content
    assert 'FIRST_PRIVATE' not in str(result)
    assert 'SECOND_PRIVATE' not in str(result)


def test_two_external_calls_have_independent_decisions(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    first = tmp_path / 'first.md'
    second = tmp_path / 'second.md'
    first.write_text('FIRST_PRIVATE', encoding='utf-8')
    second.write_text('SECOND_PRIVATE', encoding='utf-8')
    app = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[tool_call(first, 'first'), tool_call(second, 'second')]),
        AIMessage(content='review finished'),
    ])
    session = app.create_workspace_session('A', root)

    pending = app.run(session, [('user', 'read both')])
    assert [item['target'] for item in pending['approval_requests']] == [str(first), str(second)]
    result = app.resume_tool_approval(session, ['approve', 'reject'])

    messages = tool_messages(result)
    assert len(messages) == 2
    by_id = {message.tool_call_id: message for message in messages}
    assert {call_id: message.status for call_id, message in by_id.items()} == {
        'first': 'success', 'second': 'error',
    }
    assert by_id['first'].content == 'FIRST_PRIVATE'
    assert 'SECOND_PRIVATE' not in str(result)


def test_other_session_cannot_resume_pending_read_but_can_run(monkeypatch, tmp_path):
    first_root = tmp_path / 'first-workspace'
    second_root = tmp_path / 'second-workspace'
    first_root.mkdir()
    second_root.mkdir()
    external = tmp_path / 'external.md'
    external.write_text('FIRST_PRIVATE', encoding='utf-8')
    app = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[tool_call(external)]),
        AIMessage(content='second session answer'),
        AIMessage(content='first resumed answer'),
    ])
    first = app.create_workspace_session('A', first_root)
    second = app.create_workspace_session('A', second_root)

    pending = app.run(first, [('user', 'read external')])
    assert len(pending['approval_requests']) == 1
    with pytest.raises(application.SessionPendingApprovalError):
        app.resume_tool_approval(second, ['approve'])
    assert app.run(second, [('user', 'ordinary answer')])['messages'][-1].content == (
        'second session answer'
    )
    assert app.resume_tool_approval(first, ['approve'])['messages'][-1].content == (
        'first resumed answer'
    )


def test_file_inside_workspace_under_hidden_parent_is_allowed(monkeypatch, tmp_path):
    root = tmp_path / '.projects' / 'workspace'
    root.mkdir(parents=True)
    (root / 'visible.md').write_text('VISIBLE_CONTENT', encoding='utf-8')
    app = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[tool_call('visible.md')]),
        AIMessage(content='read finished'),
    ])
    session = app.create_workspace_session('A', root)

    result = app.run(session, [('user', 'read workspace file')])

    assert 'approval_requests' not in result
    assert tool_messages(result)[0].content == 'VISIBLE_CONTENT'


def test_session_read_allows_github_source_but_denies_external_hidden_target(
    monkeypatch, tmp_path,
):
    root = tmp_path / 'workspace'
    internal = root / '.github' / 'workflows' / 'test.yml'
    internal.parent.mkdir(parents=True)
    internal.write_text('workflow source', encoding='utf-8')
    external = tmp_path / '.github' / 'outside.yml'
    external.parent.mkdir()
    external.write_text('PRIVATE_EXTERNAL', encoding='utf-8')
    app = make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[tool_call('.github/workflows/test.yml', 'inside')]),
        AIMessage(content='inside done'),
        AIMessage(content='', tool_calls=[tool_call(external, 'outside')]),
        AIMessage(content='outside denied'),
    ], permissions={'mode': 'NoAsk', 'allow': [{'tool': 'read', 'path': '${workspace}/.github/**'}]})
    session = app.create_workspace_session('A', root)

    inside = app.run(session, [('user', 'read internal workflow')])
    outside_result = app.run(session, [('user', 'read external hidden')])

    assert 'approval_requests' not in inside
    assert tool_messages(inside)[0].content == 'workflow source'
    assert 'approval_requests' not in outside_result
    assert tool_messages(outside_result)[-1].status == 'error'
    assert 'PRIVATE_EXTERNAL' not in str(outside_result)
