"""A shared agent must receive the current session's project instructions."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

from pathlib import Path

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, SystemMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.application import cli
from naiyue_test_agent.application.project_instructions import (
    BASE_SYSTEM_PROMPT, ProjectInstructionsError,
)
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.model_access import ModelCatalog


class RecordingModel(FakeMessagesListChatModel):
    inputs: list[list] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.inputs.append(messages)
        return super()._generate(messages, *args, **kwargs)


def build_app(monkeypatch, model, tools=None, prompt='BASE'):
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    return application.build_local_application(
        Path('synthetic.json'), tools=tools or [],
        system_prompt=prompt, response_format=None,
    )


def system_text(messages):
    systems = [message for message in messages if isinstance(message, SystemMessage)]
    assert len(systems) == 1
    return systems[0].content


def test_cli_uses_approved_base_prompt_and_current_directory_tools(monkeypatch, capsys):
    model = RecordingModel(responses=[AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    entries = iter(['hello', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _prompt='': next(entries))

    assert cli.main(['--config', 'synthetic.json']) == 0
    assert 'done' in capsys.readouterr().out
    assert system_text(model.inputs[0]).startswith(BASE_SYSTEM_PROMPT)
    assert 'Available tools:' in system_text(model.inputs[0])
    assert '\nread:' in system_text(model.inputs[0])
    assert BASE_SYSTEM_PROMPT.startswith(
        '你是在naiyue-test-agent中工作的通用编程与测试助手'
    )


def test_workspace_file_precedence_and_session_isolation(monkeypatch, tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    empty = tmp_path / 'empty'
    for root in (first, second, empty):
        root.mkdir()
    (first / 'AGENTS.md').write_text('FIRST_AGENTS_SECRET', encoding='utf-8')
    (first / 'CLAUDE.md').write_text('FIRST_CLAUDE_IGNORED', encoding='utf-8')
    (second / 'CLAUDE.md').write_text('SECOND_CLAUDE_SECRET', encoding='utf-8')
    model = RecordingModel(responses=[AIMessage(content='ok')] * 4)
    app = build_app(monkeypatch, model)

    for root in (first, second, empty):
        app.run(app.create_workspace_session('A', root), [('user', 'hello')])
    app.run(app.create_session('A'), [('user', 'hello')])

    prompts = [system_text(messages) for messages in model.inputs]
    assert 'FIRST_AGENTS_SECRET' in prompts[0]
    assert 'FIRST_CLAUDE_IGNORED' not in prompts[0]
    assert 'SECOND_CLAUDE_SECRET' in prompts[1]
    assert 'FIRST_AGENTS_SECRET' not in prompts[1]
    assert prompts[2:] == ['BASE', 'BASE']


def test_project_instructions_are_frozen_for_one_run_and_reloaded_next_run(
    monkeypatch, tmp_path,
):
    instruction_file = tmp_path / 'AGENTS.md'
    instruction_file.write_text('VERSION_ONE', encoding='utf-8')

    @tool
    def update_instructions() -> str:
        """Change the synthetic project instructions."""
        instruction_file.write_text('VERSION_TWO', encoding='utf-8')
        return 'updated'

    model = RecordingModel(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'update_instructions', 'args': {}, 'id': 'update-1',
        }]),
        AIMessage(content='first run done'),
        AIMessage(content='second run done'),
    ])
    app = build_app(monkeypatch, model, tools=[ToolSpec(update_instructions, update_instructions.args_schema, permissions=ToolPermission(default='allow'))])
    session = app.create_workspace_session('A', tmp_path)

    app.run(session, [('user', 'update')])
    app.run(session, [('user', 'continue')])

    prompts = [system_text(messages) for messages in model.inputs]
    assert 'VERSION_ONE' in prompts[0]
    assert 'VERSION_ONE' in prompts[1]
    assert 'VERSION_TWO' not in prompts[1]
    assert 'VERSION_TWO' in prompts[2]


def test_approval_resume_starts_new_prompt_snapshot(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'AGENTS.md').write_text('BEFORE_APPROVAL', encoding='utf-8')
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside content', encoding='utf-8')
    model = RecordingModel(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'read', 'args': {'path': str(outside)}, 'id': 'outside-read',
        }]),
        AIMessage(content='done'),
    ])
    app = build_app(monkeypatch, model, tools=[create_session_read_tool()])
    session = app.create_workspace_session('A', root)

    paused = app.run(session, [('user', 'read outside')])
    assert paused['approval_requests'][0]['tool_call_id'] == 'outside-read'
    (root / 'AGENTS.md').write_text('AFTER_APPROVAL', encoding='utf-8')
    resumed = app.resume_tool_approval(session, ['approve'])

    assert resumed['messages'][-1].content == 'done'
    assert 'BEFORE_APPROVAL' in system_text(model.inputs[0])
    assert 'AFTER_APPROVAL' in system_text(model.inputs[1])


@pytest.mark.parametrize('bad_file', ['outside_link', 'invalid_utf8', 'oversized'])
def test_invalid_preferred_file_fails_before_model_and_releases_session(
    monkeypatch, tmp_path, caplog, bad_file,
):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'CLAUDE.md').write_text('MUST_NOT_FALL_BACK', encoding='utf-8')
    preferred = root / 'AGENTS.md'
    if bad_file == 'outside_link':
        outside = tmp_path / 'private.txt'
        outside.write_text('PRIVATE_BODY', encoding='utf-8')
        preferred.symlink_to(outside)
    elif bad_file == 'invalid_utf8':
        preferred.write_bytes(b'PRIVATE_BODY\xff')
    else:
        preferred.write_bytes(b'PRIVATE_BODY' + b'X' * (64 * 1024))
    model = RecordingModel(responses=[AIMessage(content='recovered')])
    app = build_app(monkeypatch, model)
    session = app.create_workspace_session('A', root)

    with pytest.raises(ProjectInstructionsError):
        app.run(session, [('user', 'first')])
    assert model.inputs == []
    assert 'PRIVATE_BODY' not in caplog.text
    assert 'MUST_NOT_FALL_BACK' not in caplog.text

    preferred.unlink()
    app.run(session, [('user', 'second')])
    assert 'MUST_NOT_FALL_BACK' in system_text(model.inputs[0])
