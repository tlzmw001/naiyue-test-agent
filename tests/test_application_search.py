"""find and grep are connected to the session loop and approval resume."""

from native_application_helpers import async_test, build_application

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.tools import (
    create_session_find_tool, create_session_grep_tool, create_session_ls_tool,
    create_session_read_tool,
)
from naiyue_test_agent.model_access import ModelCatalog


class RecordingFake(FakeMessagesListChatModel):
    seen: list[list[object]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


async def build_app(monkeypatch, responses):
    model = RecordingFake(responses=responses)
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        Path('synthetic.json'),
        tools=[create_session_read_tool(), create_session_ls_tool(),
               create_session_find_tool(), create_session_grep_tool()],
        system_prompt='', response_format=None,
    )
    return app, model


def call(name, args, call_id):
    return {'name': name, 'args': args, 'id': call_id}


def tool_messages(result):
    return [item for item in result['messages'] if isinstance(item, ToolMessage)]


@async_test
async def test_find_and_grep_tool_results_enter_next_model_turn(monkeypatch, tmp_path):
    (tmp_path / 'contract.md').write_text('discount rule\n')
    app, model = await build_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('find', {'pattern': '*.md'}, 'find-ok'),
            call('grep', {'pattern': 'discount'}, 'grep-ok'),
            call('grep', {'pattern': 'x', 'path': '.env'}, 'grep-error'),
        ]),
        AIMessage(content='I found the contract'),
    ])
    session = await app.sessions.create_workspace('A', tmp_path)
    await app.sessions.select_permission_mode(session, 'NoAsk')

    result = await app.runs.start(session, [('user', 'find the discount contract')])

    by_id = {item.tool_call_id: item for item in tool_messages(result)}
    assert by_id['find-ok'].content == 'contract.md'
    assert by_id['grep-ok'].content == 'contract.md:1: discount rule'
    assert by_id['grep-error'].status == 'error'
    assert {item.tool_call_id for item in model.seen[1]
            if isinstance(item, ToolMessage)} == set(by_id)
    assert result['messages'][-1].content == 'I found the contract'


@async_test
async def test_find_and_grep_external_approvals_bind_separate_targets(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside_dir = tmp_path / 'outside'
    outside_dir.mkdir()
    outside_file = outside_dir / 'notes.md'
    outside_file.write_text('APPROVED_NEEDLE\n')
    app, model = await build_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('find', {'pattern': '*.md', 'path': str(outside_dir)}, 'find-out'),
            call('grep', {'pattern': 'NEEDLE', 'path': str(outside_file)}, 'grep-out'),
        ]),
        AIMessage(content='reviewed'),
    ])
    session = await app.sessions.create_workspace('A', root)

    pending = await app.runs.start(session, [('user', 'search outside')])
    assert [item['tool_name'] for item in pending['approval_requests']] == [
        'find', 'grep',
    ]
    assert tool_messages(pending) == []
    result = await app.runs.resume_approval(session, ['approve', 'reject'])
    by_id = {item.tool_call_id: item for item in tool_messages(result)}
    assert by_id['find-out'].content == 'notes.md'
    assert by_id['grep-out'].status == 'error'
    assert 'APPROVED_NEEDLE' not in str(result)
    assert {item.tool_call_id for item in model.seen[-1]
            if isinstance(item, ToolMessage)} == {'find-out', 'grep-out'}


@async_test
async def test_grep_approval_target_change_returns_tool_error(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    first = tmp_path / 'first.md'
    second = tmp_path / 'second.md'
    first.write_text('FIRST_PRIVATE\n')
    second.write_text('SECOND_PRIVATE\n')
    alias = root / 'alias.md'
    alias.symlink_to(first)
    app, _model = await build_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('grep', {'pattern': 'PRIVATE', 'path': str(alias)}, 'grep-out'),
        ]),
        AIMessage(content='target changed'),
    ])
    session = await app.sessions.create_workspace('A', root)

    pending = await app.runs.start(session, [('user', 'search alias')])
    assert pending['approval_requests'][0]['target'] == str(first)
    alias.unlink()
    alias.symlink_to(second)
    result = await app.runs.resume_approval(session, ['approve'])
    assert tool_messages(result)[0].status == 'error'
    assert 'approval' in tool_messages(result)[0].content.lower()
    assert 'FIRST_PRIVATE' not in str(result)
    assert 'SECOND_PRIVATE' not in str(result)


@async_test
async def test_all_four_read_only_tools_share_one_review_batch(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'external'
    outside.mkdir()
    note = outside / 'note.md'
    note.write_text('NEEDLE\n', encoding='utf-8')
    app, _model = await build_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('read', {'path': str(note)}, 'read'),
            call('ls', {'path': str(outside)}, 'ls'),
            call('find', {'pattern': '*.md', 'path': str(outside)}, 'find'),
            call('grep', {'pattern': 'NEEDLE', 'path': str(note)}, 'grep'),
        ]),
        AIMessage(content='reviewed all tools'),
    ])
    session = await app.sessions.create_workspace('A', root)

    pending = await app.runs.start(session, [('user', 'inspect reviewed targets')])
    assert [item['tool_name'] for item in pending['approval_requests']] == [
        'read', 'ls', 'find', 'grep',
    ]
    result = await app.runs.resume_approval(session, ['approve'] * 4)
    by_id = {item.tool_call_id: item for item in tool_messages(result)}
    assert {item.status for item in by_id.values()} == {'success'}
    assert by_id['read'].content == 'NEEDLE\n'
    assert by_id['ls'].content == 'note.md'
    assert by_id['find'].content == 'note.md'
    assert by_id['grep'].content == 'note.md:1: NEEDLE'
