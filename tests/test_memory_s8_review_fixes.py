"""Failure reports remain complete and the frozen baseline remains runnable."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from langchain_core.messages import AIMessage
from test_async_application import StreamingToolModel,call


def test_setup_failure_has_one_row_per_case(monkeypatch,tmp_path):
    from scripts.memory_foreground_evaluation import run_batch
    def fail(_):raise OSError('private')
    monkeypatch.setattr('naiyue_test_agent.model_access.load_model_catalog',fail)
    args=SimpleNamespace(config=tmp_path/'unused',output=tmp_path/'out',model_id=None,fixtures=None,protocol=None,suite='space-time')
    assert asyncio.run(run_batch(args))==1
    report=json.loads(args.output.read_text())
    assert len(report['cases'])==report['expected_cases']==8
    assert report['structural_failures']==8
    assert len({c['case_id'] for c in report['cases']})==8


@pytest.mark.parametrize('value', [None, 'bad', []])
def test_bad_request_shape_keeps_feedback_and_followup(value):
    from test_memory_foreground_evaluation import wiki_case, run_case

    model = StreamingToolModel(replies=[
        call('memory', {'request': value}), AIMessage(content='rejected'), AIMessage(content='unknown')])
    result = run_case(wiki_case(), model)
    observed = result['observations']
    assert result['status'] == 'completed' and observed['logical_calls'] == 2
    assert observed['tool_feedback'][0]['status'] == 'error'
    assert observed['followup']['status'] == 'completed' and observed['followup']['logical_calls'] == 1
    assert observed['memories'] == []


def test_primary_failure_survives_snapshot_failure(monkeypatch):
    from scripts import memory_wiki_runtime_evaluation as runner
    from test_memory_foreground_evaluation import wiki_case, run_case

    original = runner._memory_snapshot
    count = 0

    def snapshot(access):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('private snapshot')
        return original(access)

    monkeypatch.setattr(runner, '_memory_snapshot', snapshot)
    model = StreamingToolModel(replies=[RuntimeError('private primary')])
    result = run_case(wiki_case(), model)
    observed = result['observations']
    assert observed['stage'] == 'foreground_turn' and observed['error_type'] == 'RuntimeError'
    assert observed['snapshot_error'] == 'OSError'
    assert observed['logical_calls'] == 1 and 'memories' not in observed
    assert 'private' not in json.dumps(result)


def test_current_manifest_changes_when_wiki_source_is_missing(monkeypatch, tmp_path):
    from scripts import memory_wiki_evaluation as module
    import subprocess

    name = 'naiyue_test_agent/tools/memory_wiki_actions.py'
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_text('fixture source')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **kw: (name + '\0').encode())
    valid = module.provenance()
    path.unlink()
    missing = module.provenance()
    assert valid['source_files'] == 1 and missing['source_files'] == 0
    assert valid['source_sha256'] != missing['source_sha256']


@pytest.mark.parametrize('split', ['calibration', 'validation', 'holdout'])
def test_foreground_runs_selected_complete_split(monkeypatch, tmp_path, split):
    from scripts import memory_wiki_runtime_evaluation as runner
    from scripts.memory_wiki_evaluation import load_suite
    from test_memory_foreground_evaluation import batch_args, fake_catalog

    fake_catalog(monkeypatch, [AIMessage(content='done') for _ in range(16)])
    report = asyncio.run(runner.run(batch_args(tmp_path, split=split)))
    expected = [c for c in load_suite()[0] if c['split'] == split]
    assert report['split'] == split and len(report['cases']) == 8
    assert [c['case_id'] for c in report['cases']] == [c['case_id'] for c in expected]
    applicable = [c for c in expected if 'foreground' in c['input']['scenario']['execution_modes']]
    assert [c['status'] for c in report['cases']].count('completed') == len(applicable)
    assert report['model_requests'] == len(applicable) + sum(
        bool(c['input']['scenario'].get('followup')) for c in applicable)
    assert 'max_logical_calls' not in report


def test_invalid_split_is_rejected_before_loading_model(monkeypatch,tmp_path):
    from scripts.memory_foreground_evaluation import run_batch
    seen=[]
    monkeypatch.setattr('naiyue_test_agent.model_access.load_model_catalog',lambda _:seen.append(True))
    args=SimpleNamespace(config=tmp_path/'unused',output=tmp_path/'out',model_id=None,fixtures=None,protocol=None,suite='space-time',split='all')
    with pytest.raises(ValueError,match='split'):asyncio.run(run_batch(args))
    assert seen==[] and not args.output.exists()
