"""Installed command shape uses the same source, capture and cache production functions."""
import json
from pathlib import Path
from naiyue_test_agent.cli import main
from test_git_source_plan import repository


def test_source_plan_and_cached_capture_are_real_cli_commands(tmp_path,capsys):
    root=repository(tmp_path);plan=tmp_path/'plan.json';cache=tmp_path/'cache'
    assert main(['cloud','input','select','--source',str(root),'--output',str(plan)])==0
    assert plan.exists() and json.loads(capsys.readouterr().out)['files']==5
    receipts=[]
    for _ in range(2):
        assert main(['cloud','input','capture','--source',str(root),'--plan',str(plan),'--cache',str(cache)])==0
        receipts.append(json.loads(capsys.readouterr().out))
    assert receipts[0]['input_id']==receipts[1]['input_id'] and receipts[0]['source_receipt']!=receipts[1]['source_receipt']
    assert all(Path(r['source_receipt']).exists() for r in receipts)
