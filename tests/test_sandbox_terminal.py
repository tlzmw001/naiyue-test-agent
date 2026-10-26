"""Terminal execution selection is explicit and cannot replace a saved binding."""
import pytest
from naiyue_test_agent.cli.chat import main


@pytest.mark.parametrize('args',[
    ['--no-session','--execution','sandbox'],
    ['--resume','S','--execution','sandbox'],
    ['--include-untracked','notes.txt'],
])
def test_terminal_rejects_incompatible_environment_selection_before_connection(args):
    with pytest.raises(SystemExit) as caught:main(args)
    assert caught.value.code==2


def test_terminal_accepts_explicit_sandbox_and_selected_input(monkeypatch):
    from naiyue_test_agent.cli import chat
    seen=[]
    def prepare(args,settings):
        seen.append((args.execution,args.include_untracked));return None,None,None
    monkeypatch.setattr(chat,'prepare_terminal',prepare)
    monkeypatch.setattr(chat,'load_settings',lambda:None)
    assert main(['--execution','sandbox','--include-untracked','notes.txt'])==0
    assert seen==[('sandbox',['notes.txt'])]
