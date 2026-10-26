"""End-to-end terminal input uses TCP and keeps temporary chats in RAM."""
from langchain_core.messages import AIMessage
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.cli import chat
from test_async_application import StreamingToolModel
from terminal_service_helpers import isolated_terminal_launch


def test_temporary_chat_entry_through_actual_service(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {
        'A': StreamingToolModel(replies=[AIMessage(content='answer')])}))
    lines = iter(['question', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _='': next(lines))
    with isolated_terminal_launch(monkeypatch):
        assert chat.main(['--config', 'unused', '--no-session']) == 0
    output = capsys.readouterr().out
    assert 'answer' in output and '本次运行完成' in output
    assert not list(tmp_path.rglob('sessions.sqlite'))
