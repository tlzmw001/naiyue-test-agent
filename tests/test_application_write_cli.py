"""The CLI exposes the workspace write tool and uncertain-outcome guidance."""

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.application import cli
from naiyue_test_agent.agent_runtime.tools import write as write_module
from naiyue_test_agent.model_access import ModelCatalog


class RecordingFake(FakeMessagesListChatModel):
    seen: list[list[tuple[str, str]]] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append([(message.type, message.content) for message in messages])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def feed(monkeypatch, *lines):
    entries = iter(lines)
    monkeypatch.setattr('builtins.input', lambda _prompt='': next(entries))


def test_cli_workspace_registers_write_and_creates_file(monkeypatch, capsys, tmp_path):
    class ToolCapableFake(RecordingFake):
        def bind_tools(self, tools, **kwargs):
            return self

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'write',
            'args': {'path': 'case.md', 'content': '# Discount case\n'},
            'id': 'write-call',
        }]),
        AIMessage(content='case saved'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'save this case', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0

    assert (tmp_path / 'case.md').read_text(encoding='utf-8') == '# Discount case\n'
    assert 'case saved' in capsys.readouterr().out
    assert ('tool', 'write created 16 bytes') in model.seen[1]


def test_cli_uncertain_write_tells_user_to_inspect_before_retry(
    monkeypatch, capsys, tmp_path,
):
    class ToolCapableFake(RecordingFake):
        def bind_tools(self, tools, **kwargs):
            return self

    model = ToolCapableFake(responses=[AIMessage(content='', tool_calls=[{
        'name': 'write',
        'args': {'path': 'case.md', 'content': 'PRIVATE_NEW_CONTENT'},
        'id': 'write-unknown',
    }])])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))

    def unknown_replace(_source, _target):
        raise OSError('PRIVATE_DISK_DETAIL')

    monkeypatch.setattr(write_module.os, 'replace', unknown_replace)
    feed(monkeypatch, 'write case', 'try again', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert '请先检查' in output
    assert '勿直接重试' in output
    assert '本会话' in output
    assert 'PRIVATE_' not in output
    assert len(model.seen) == 1
