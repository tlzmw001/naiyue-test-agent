"""ls is wired through the same session, tool-result, and approval path as Read."""

from native_application_helpers import async_test, build_application

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.tools import (
    create_session_ls_tool, create_session_read_tool,
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
        tools=[create_session_read_tool(), create_session_ls_tool()],
        system_prompt='', response_format=None,
    )
    return app, model


def call(name, path, call_id):
    return {'name': name, 'args': {'path': str(path)}, 'id': call_id}


def tool_messages(result):
    return [item for item in result['messages'] if isinstance(item, ToolMessage)]


@async_test
async def test_session_ls_result_and_error_return_to_next_model_turn(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'a.md').write_text('a')
    app, model = await build_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('ls', '.', 'good'),
                                          call('ls', 'missing', 'bad')]),
        AIMessage(content='I found a.md'),
    ])
    session = await app.acreate_workspace_session('A', root)

    result = await app.arun(session, [('user', 'list files')])

    by_id = {item.tool_call_id: item for item in tool_messages(result)}
    assert by_id['good'].content == 'a.md'
    assert by_id['good'].status == 'success'
    assert by_id['bad'].status == 'error'
    assert 'path' in by_id['bad'].content
    assert {item.tool_call_id for item in model.seen[1]
            if isinstance(item, ToolMessage)} == {'good', 'bad'}
    assert result['messages'][-1].content == 'I found a.md'


@async_test
async def test_external_ls_requires_approval_and_revalidates_target(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    first = tmp_path / 'first'
    first.mkdir()
    (first / 'one.md').write_text('one')
    second = tmp_path / 'second'
    second.mkdir()
    (second / 'two.md').write_text('two')
    alias = root / 'alias'
    alias.symlink_to(first, target_is_directory=True)
    app, _model = await build_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('ls', alias, 'outside')]),
        AIMessage(content='handled'),
    ])
    session = await app.acreate_workspace_session('A', root)

    pending = await app.arun(session, [('user', 'list alias')])
    assert pending['approval_requests'] == [{
        'tool_call_id': 'outside', 'tool_name': 'ls', 'target': str(first),
    }]
    assert tool_messages(pending) == []
    alias.unlink()
    alias.symlink_to(second, target_is_directory=True)
    result = await app.aresume_tool_approval(session, ['approve'])
    assert tool_messages(result)[0].status == 'error'
    assert 'approval' in tool_messages(result)[0].content.lower()
    assert 'one.md' not in str(result) and 'two.md' not in str(result)


@async_test
async def test_mixed_read_and_ls_approval_resumes_each_call(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside_dir = tmp_path / 'external'
    outside_dir.mkdir()
    (outside_dir / 'listed.md').write_text('listed')
    outside_file = tmp_path / 'external.md'
    outside_file.write_text('APPROVED_TEXT')
    app, _model = await build_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('ls', outside_dir, 'dir'),
                                          call('read', outside_file, 'file')]),
        AIMessage(content='finished'),
    ])
    session = await app.acreate_workspace_session('A', root)

    pending = await app.arun(session, [('user', 'inspect external inputs')])
    assert [item['tool_name'] for item in pending['approval_requests']] == [
        'ls', 'read',
    ]
    result = await app.aresume_tool_approval(session, ['approve', 'reject'])
    by_id = {item.tool_call_id: item for item in tool_messages(result)}
    assert by_id['dir'].content == 'listed.md'
    assert by_id['file'].status == 'error'
    assert 'APPROVED_TEXT' not in str(result)


@async_test
async def test_session_workspaces_are_isolated_for_ls(monkeypatch, tmp_path):
    first_root = tmp_path / 'first'
    second_root = tmp_path / 'second'
    first_root.mkdir()
    second_root.mkdir()
    (first_root / 'FIRST.md').write_text('x')
    (second_root / 'SECOND.md').write_text('x')
    app, _model = await build_app(monkeypatch, [
        AIMessage(content='', tool_calls=[call('ls', '.', 'first-list')]),
        AIMessage(content='first done'),
        AIMessage(content='', tool_calls=[call('ls', '.', 'second-list')]),
        AIMessage(content='second done'),
    ])
    first = await app.acreate_workspace_session('A', first_root)
    second = await app.acreate_workspace_session('A', second_root)

    one = await app.arun(first, [('user', 'list')])
    two = await app.arun(second, [('user', 'list')])
    assert tool_messages(one)[0].content == 'FIRST.md'
    assert tool_messages(two)[0].content == 'SECOND.md'
