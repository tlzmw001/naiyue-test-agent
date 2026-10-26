"""The CLI makes edit available in a selected workspace."""

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.cli import chat as cli
from naiyue_test_agent.model_access import ModelCatalog


class EditFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_cli_workspace_registers_edit(monkeypatch, tmp_path, capsys):
    target = tmp_path / 'case.md'
    target.write_text('old', encoding='utf-8')
    model = EditFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'edit', 'id': 'edit-call', 'args': {
                'path': 'case.md',
                'edits': [{'oldText': 'old', 'newText': 'new'}],
            },
        }]),
        AIMessage(content='edited'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    entries = iter(('edit the case', ':quit'))
    monkeypatch.setattr('builtins.input', lambda _prompt='': next(entries))

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0
    assert target.read_text(encoding='utf-8') == 'new'
    assert 'edited' in capsys.readouterr().out
