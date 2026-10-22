"""Installed/source continuation acceptance and report rejection share one contract."""

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
    from scripts.release_subagent_smoke import subagent_scenario
    return asyncio.run(subagent_scenario(tmp_path_factory.mktemp('continuation'), StreamingToolModel))


def test_shared_worker_continues_after_reopen_and_keeps_new_instances_independent(report):
    from scripts.release_subagent_smoke import verify_subagent_report
    verify_subagent_report(report)
    rows = report['continuations']
    assert len({row['agent_id'] for row in rows}) == 1
    assert [row['model_calls'] for row in rows] == [2, 2, 2]
    assert [row['previous_reports'] for row in rows] == [[], ['report-1'], ['report-1', 'report-2']]
    assert report['fresh']['previous_reports'] == []
    assert report['fresh']['agent_id'] != rows[0]['agent_id']
    assert report['child_model_calls'] == 8 and report['write_calls'] == 4
    assert report['reopen'] == dict(model_calls=0, write_calls=0, auto_calls=0,
        drivers=0, approvals=0, history_ids_equal=True)
    assert report['provider_requests'] == 0


@pytest.mark.parametrize('mutation', ['missing', 'thread', 'identity', 'model_count',
    'history', 'checkpoint', 'delegation', 'write_count', 'restore', 'list'])
def test_report_checker_rejects_incomplete_or_tampered_facts(report, mutation):
    from scripts.release_subagent_smoke import verify_subagent_report
    broken = deepcopy(report)
    rows = broken['continuations']
    if mutation == 'missing': del broken['continuations']
    elif mutation == 'thread': rows[2]['child_thread_id'] = 'subagent:' + 'f' * 64
    elif mutation == 'identity': rows[2]['agent_id'] = 'f' * 64
    elif mutation == 'model_count': rows[2]['model_calls'] = 0
    elif mutation == 'history': rows[2]['previous_reports'] = []
    elif mutation == 'checkpoint': rows[2]['checkpoint_id'] = rows[0]['checkpoint_id']
    elif mutation == 'delegation': rows[2]['delegation_id'] = rows[0]['delegation_id']
    elif mutation == 'write_count': broken['write_calls'] = 5
    elif mutation == 'restore': broken['reopen']['write_calls'] = 1
    elif mutation == 'list': broken['lists'] = []
    with pytest.raises(ValueError, match='Subagent acceptance'):
        verify_subagent_report(broken)


def test_worker_propagates_its_verifier_failure(monkeypatch, tmp_path):
    from scripts import release_subagent_smoke
    def rejected(_report):
        raise AssertionError('injected continuation verifier failure')
    monkeypatch.setattr(release_subagent_smoke, 'verify_subagent_report', rejected)
    with pytest.raises(AssertionError, match='injected continuation verifier'):
        asyncio.run(release_subagent_smoke.subagent_scenario(tmp_path, StreamingToolModel))


@pytest.mark.parametrize('mutation', ['missing', 'empty', 'thread', 'model_count', 'omitted_case'])
def test_installer_rejects_missing_acceptance_before_writing_success(monkeypatch, tmp_path, report, mutation):
    from scripts import verify_wheel_install as installer
    payload = {'subagent_continuation': deepcopy(report)}
    if mutation == 'missing': payload.clear()
    elif mutation == 'empty': payload['subagent_continuation'] = {}
    elif mutation == 'thread': payload['subagent_continuation']['continuations'][2]['child_thread_id'] = 'wrong-thread'
    elif mutation == 'model_count': payload['subagent_continuation']['child_model_calls'] = 0
    elif mutation == 'omitted_case': payload['subagent_continuation']['continuations'].pop()
    wheel, output = tmp_path / 'candidate.whl', tmp_path / 'result.json'
    wheel.write_bytes(b'fixture')
    info = dict(version='0.2.0', sha256='a' * 64)
    monkeypatch.setattr(installer, 'inspect_wheel', lambda _: info)
    monkeypatch.setattr(installer.venv.EnvBuilder, 'create', lambda self, path: path.mkdir())
    def fake_run(command, **_kwargs):
        if command[1:4] == ['-m', 'pip', 'install']:
            Path(command[command.index('--report') + 1]).write_text(json.dumps({'install': [{
                'metadata': {'name': 'naiyue-test-agent', 'version': info['version']},
                'download_info': {'archive_info': {'hashes': {'sha256': info['sha256']}}}}]}))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr='')
    monkeypatch.setattr(installer.subprocess, 'run', fake_run)
    monkeypatch.setattr(sys, 'argv', ['verify_wheel_install', '--wheel', str(wheel), '--output', str(output)])
    with pytest.raises(ValueError, match='Subagent acceptance'):
        installer.main()
    assert not output.exists()
