"""Workspace read/search tools and approvals through the CLI."""

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.cli import chat as cli
from naiyue_test_agent.model_access import ModelCatalog


class RecordingFake(FakeMessagesListChatModel):
    seen: list[list[tuple[str, str]]] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append([(message.type, message.content) for message in messages])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def feed(monkeypatch, *lines):
    entries = iter(lines)
    monkeypatch.setattr('builtins.input', lambda _prompt='': next(entries))


def test_cli_explicit_workspace_registers_read_and_returns_result_to_model(
    monkeypatch, capsys, tmp_path,
):
    class ToolCapableFake(RecordingFake):
        def bind_tools(self, tools, **kwargs):
            return self

    document = tmp_path / 'contract.md'
    document.write_text('discount contract', encoding='utf-8')
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'read', 'args': {'path': 'contract.md'}, 'id': 'call-1',
        }]),
        AIMessage(content='I read the contract'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'read the contract', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert 'I read the contract' in output
    assert 'discount contract' not in output
    assert model.seen[1][-1] == ('tool', 'discount contract')


def test_cli_explicit_workspace_registers_ls_and_returns_hidden_names_to_model(
    monkeypatch, capsys, tmp_path,
):
    class ToolCapableFake(RecordingFake):
        def bind_tools(self, tools, **kwargs):
            return self

    (tmp_path / '.env').write_text('PRIVATE_VALUE', encoding='utf-8')
    (tmp_path / 'contract.md').write_text('contract', encoding='utf-8')
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'ls', 'args': {}, 'id': 'list-call',
        }]),
        AIMessage(content='listing received'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'list names', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert 'listing received' in output
    assert 'PRIVATE_VALUE' not in output
    assert model.seen[1][-1] == ('tool', '.env\ncontract.md')


def test_cli_workspace_registers_find_and_grep(monkeypatch, capsys, tmp_path):
    class ToolCapableFake(RecordingFake):
        def bind_tools(self, tools, **kwargs):
            return self

    (tmp_path / 'contract.md').write_text('discount rule\n', encoding='utf-8')
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'find', 'args': {'pattern': '*.md'}, 'id': 'find-call'},
            {'name': 'grep', 'args': {'pattern': 'discount'}, 'id': 'grep-call'},
        ]),
        AIMessage(content='search completed'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'find discount contract', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0

    assert 'search completed' in capsys.readouterr().out
    assert ('tool', 'contract.md') in model.seen[1]
    assert ('tool', 'contract.md:1: discount rule') in model.seen[1]


def test_cli_model_can_request_a_read_page(monkeypatch, capsys, tmp_path):
    class ToolCapableFake(RecordingFake):
        def bind_tools(self, tools, **kwargs):
            return self

    (tmp_path / 'contract.md').write_text('first\nsecond\n', encoding='utf-8')
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'read',
            'args': {'path': 'contract.md', 'offset': 1, 'limit': 1},
            'id': 'call-page',
        }]),
        AIMessage(content='Page received'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'read one line', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0
    assert 'Page received' in capsys.readouterr().out
    assert model.seen[1][-1] == ('tool', 'first\n[read: lines 1-1; next_offset=2]\n[read: stopped by 1 line page limit; continue with the same path, offset=2, limit=1]')


def test_cli_external_read_requires_explicit_approval_and_resumes(monkeypatch, capsys, tmp_path):
    class ToolCapableFake(RecordingFake):
        def bind_tools(self, tools, **kwargs):
            return self

    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'external.md'
    outside.write_text('PRIVATE_APPROVED', encoding='utf-8')
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'read', 'args': {'path': str(outside)}, 'id': 'read-outside',
        }]),
        AIMessage(content='approved response'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'read outside', ':approve', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace', str(root)]) == 0

    output = capsys.readouterr().out
    assert '待审批' in output
    assert str(outside) in output
    assert 'approved response' in output
    assert 'PRIVATE_APPROVED' not in output
    assert model.seen[1][-1] == ('tool', 'PRIVATE_APPROVED')


def test_cli_review_two_calls_and_reject_invalid_count(monkeypatch, capsys, tmp_path):
    class ToolCapableFake(RecordingFake):
        def bind_tools(self, tools, **kwargs):
            return self

    root = tmp_path / 'workspace'
    root.mkdir()
    first = tmp_path / 'first.md'
    second = tmp_path / 'second.md'
    first.write_text('FIRST_PRIVATE', encoding='utf-8')
    second.write_text('SECOND_PRIVATE', encoding='utf-8')
    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(first)}, 'id': 'first'},
            {'name': 'read', 'args': {'path': str(second)}, 'id': 'second'},
        ]),
        AIMessage(content='review completed'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'read both', ':review approve', ':review approve reject', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace', str(root)]) == 0

    output = capsys.readouterr().out
    assert '需要为 2 个调用分别给出决定' in output
    assert 'review completed' in output
    assert 'FIRST_PRIVATE' not in output
    assert 'SECOND_PRIVATE' not in output
    assert model.seen[1].count(('tool', 'FIRST_PRIVATE')) == 1
    assert not any('SECOND_PRIVATE' in content for _kind, content in model.seen[1])
