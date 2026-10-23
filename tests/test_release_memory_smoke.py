"""Installed memory acceptance must exercise its production chain and fail closed."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from test_async_application import StreamingToolModel


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    from scripts.release_memory_smoke import memory_scenario
    return asyncio.run(memory_scenario(tmp_path_factory.mktemp('memory-installed'), StreamingToolModel))


def test_memory_worker_uses_the_real_graph_and_reopens_without_replay(report):
    from scripts.release_memory_smoke import verify_memory_report
    verify_memory_report(report)
    assert report['status'] == 'passed'
    assert report['worker_model_calls'] == 1 and report['root_model_calls'] == 3
    assert report['memory_records'] == 1 and report['overview_version'] == 1
    assert report['memory_input_tokens'] == 31
    assert report['task_id'] not in report['root_run_ids']
    assert report['cold_model_calls'] == report['cold_tasks'] == report['provider_requests'] == 0


@pytest.mark.parametrize('mutation', ['missing', 'status', 'worker', 'overview', 'identity', 'cold', 'usage'])
def test_report_rejects_missing_or_inconsistent_memory_acceptance(report, mutation):
    from scripts.release_memory_smoke import verify_memory_report
    broken = deepcopy(report)
    if mutation == 'missing': broken.clear()
    elif mutation == 'status': broken['status'] = 'failed'
    elif mutation == 'worker': broken['worker_model_calls'] = 0
    elif mutation == 'overview': broken['overview_version'] = 0
    elif mutation == 'identity': broken['task_id'] = broken['root_run_ids'][0]
    elif mutation == 'cold': broken['cold_model_calls'] = 1
    elif mutation == 'usage': broken['memory_input_tokens'] = True
    with pytest.raises(ValueError, match='Memory acceptance'):
        verify_memory_report(broken)


def test_memory_worker_does_not_swallow_its_acceptance_failure(monkeypatch, tmp_path):
    from scripts import release_memory_smoke
    def rejected(_report):
        raise AssertionError('injected memory acceptance failure')
    monkeypatch.setattr(release_memory_smoke, 'verify_memory_report', rejected)
    with pytest.raises(AssertionError, match='injected memory acceptance failure'):
        asyncio.run(release_memory_smoke.memory_scenario(tmp_path, StreamingToolModel))


@pytest.mark.parametrize('payload', [{}, {'memory': {}}, {'memory': {'status': 'passed'}}])
def test_installer_rejects_missing_memory_facts_before_writing_success(monkeypatch, tmp_path, payload):
    from scripts import verify_wheel_install as installer
    wheel, output = tmp_path / 'candidate.whl', tmp_path / 'install.json'
    wheel.write_bytes(b'fixture')
    info = dict(version='0.2.0', sha256='a' * 64)
    monkeypatch.setattr(installer, 'inspect_wheel', lambda _: info)
    monkeypatch.setattr(installer, 'verify_subagent_report', lambda _: None)
    monkeypatch.setattr(installer.venv.EnvBuilder, 'create', lambda self, path: path.mkdir())
    def fake_run(command, **_kwargs):
        if command[1:4] == ['-m', 'pip', 'install']:
            Path(command[command.index('--report') + 1]).write_text(json.dumps({'install': [{
                'metadata': {'name': 'naiyue-test-agent', 'version': info['version']},
                'download_info': {'archive_info': {'hashes': {'sha256': info['sha256']}}}}]}))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr='')
    monkeypatch.setattr(installer.subprocess, 'run', fake_run)
    monkeypatch.setattr(sys, 'argv', ['verify_wheel_install', '--wheel', str(wheel), '--output', str(output)])
    with pytest.raises(ValueError, match='Memory acceptance'):
        installer.main()
    assert not output.exists()
