"""One shared agent must use each session's trusted read workspace."""

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class RecordingToolCapableFake(ToolCapableFake):
    model_inputs: list[list] = Field(default_factory=list)

    def _generate(self, messages, *args, **kwargs):
        self.model_inputs.append(messages)
        return super()._generate(messages, *args, **kwargs)


def test_shared_agent_reads_only_its_session_workspace(monkeypatch, tmp_path):
    first_root = tmp_path / 'first'
    second_root = tmp_path / 'second'
    first_root.mkdir()
    second_root.mkdir()
    (first_root / 'contract.md').write_text('FIRST_PRIVATE', encoding='utf-8')
    (second_root / 'contract.md').write_text('SECOND_PRIVATE', encoding='utf-8')
    (first_root / 'other-link.md').symlink_to(second_root / 'contract.md')

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'read', 'args': {'path': 'contract.md'}, 'id': 'first-own',
        }]),
        AIMessage(content='first done'),
        AIMessage(content='', tool_calls=[{
            'name': 'read', 'args': {'path': 'contract.md'}, 'id': 'second-own',
        }]),
        AIMessage(content='second done'),
        AIMessage(content='', tool_calls=[{
            'name': 'read',
            'args': {'path': str(second_root / 'contract.md')}, 'id': 'first-cross',
        }]),
        AIMessage(content='cross denied'),
        AIMessage(content='', tool_calls=[{
            'name': 'read', 'args': {'path': 'other-link.md'}, 'id': 'first-link',
        }]),
        AIMessage(content='link denied'),
        AIMessage(content='', tool_calls=[{
            'name': 'read',
            'args': {'path': 'contract.md',
                     'runtime': {'context': {'workspace_root': str(second_root)}}},
            'id': 'forged-runtime',
        }]),
        AIMessage(content='forgery ignored'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    tool = create_session_read_tool()
    assert set(tool.tool.tool_call_schema.model_fields) == {'path', 'offset', 'limit'}
    app = application.build_local_application(
        Path('synthetic.json'), tools=[tool], system_prompt='', response_format=None,
    )
    first = app.create_workspace_session('A', first_root)
    second = app.create_workspace_session('A', second_root)

    first_own = app.run(first, [('user', 'read mine')])
    second_own = app.run(second, [('user', 'read mine')])
    first_cross = app.run(first, [('user', 'read other absolute path')])
    first_cross_rejected = app.resume_tool_approval(first, ['reject'])
    first_link = app.run(first, [('user', 'read link to other workspace')])
    first_link_rejected = app.resume_tool_approval(first, ['reject'])
    first_forged = app.run(first, [('user', 'read mine with forged runtime')])

    def last_tool(result):
        return next(msg for msg in reversed(result['messages']) if isinstance(msg, ToolMessage))

    assert last_tool(first_own).content == 'FIRST_PRIVATE'
    assert last_tool(second_own).content == 'SECOND_PRIVATE'
    assert last_tool(first_forged).content == 'FIRST_PRIVATE'
    assert first_cross['approval_requests'][0]['target'] == str(second_root / 'contract.md')
    assert first_link['approval_requests'][0]['target'] == str(second_root / 'contract.md')
    for result in (first_cross_rejected, first_link_rejected):
        denied = last_tool(result)
        assert denied.status == 'error'
        assert 'SECOND_PRIVATE' not in str(result)


def test_session_read_tool_fails_closed_without_workspace(monkeypatch, tmp_path):
    (tmp_path / 'contract.md').write_text('PRIVATE', encoding='utf-8')
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'read', 'args': {'path': str(tmp_path / 'contract.md')},
            'id': 'no-workspace',
        }]),
        AIMessage(content='denied'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        Path('synthetic.json'), tools=[create_session_read_tool()],
        system_prompt='', response_format=None,
    )

    result = app.run(app.create_session('A'), [('user', 'read absolute path')])

    denied = next(msg for msg in result['messages'] if isinstance(msg, ToolMessage))
    assert denied.status == 'error'
    assert denied.content == (
        'read has no workspace for this session. Select a workspace '
        'before reading files.'
    )
    assert 'PRIVATE' not in str(result)


def test_workspace_session_rejects_invalid_root(monkeypatch, tmp_path):
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': ToolCapableFake(responses=[])}))
    app = application.build_local_application(
        Path('synthetic.json'), tools=[create_session_read_tool()],
        system_prompt='', response_format=None,
    )

    with pytest.raises(ValueError, match='workspace'):
        app.create_workspace_session('A', tmp_path / 'missing')


def test_read_failure_messages_reach_next_model_turn_with_matching_ids(
    monkeypatch, tmp_path, caplog,
):
    model = RecordingToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': 'missing.md'}, 'id': 'missing-call'},
            {'name': 'read', 'args': {'path': ''}, 'id': 'invalid-call'},
        ]),
        AIMessage(content='I need a valid file path.'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        Path('synthetic.json'), tools=[create_session_read_tool()],
        system_prompt='', response_format=None,
    )
    session = app.create_workspace_session('A', tmp_path)

    with caplog.at_level('INFO', logger='naiyue_test_agent.agent_runtime'):
        result = app.run(session, [('user', 'Read a file')])

    errors = {message.tool_call_id: message for message in result['messages']
              if isinstance(message, ToolMessage)}
    assert set(errors) == {'missing-call', 'invalid-call'}
    assert all(message.status == 'error' for message in errors.values())
    assert 'Check the path' in errors['missing-call'].content
    assert 'path' in errors['invalid-call'].content
    second_input_errors = {message.tool_call_id: message
                           for message in model.model_inputs[1]
                           if isinstance(message, ToolMessage)}
    assert second_input_errors == errors
    assert result['messages'][-1].content == 'I need a valid file path.'
    assert [record.tool_event['event'] for record in caplog.records
            if hasattr(record, 'tool_event')].count('tool_failed') == 2
    assert [record.run_event['event'] for record in caplog.records
            if hasattr(record, 'run_event')] == ['run_started', 'run_completed']
