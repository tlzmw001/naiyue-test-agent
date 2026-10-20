"""Installed MCP gate exercises actual application/SDK and propagates verifier failures."""

import asyncio
import json

import pytest

from test_async_application import StreamingToolModel


def test_release_mcp_gate_covers_runtime_lifecycle_and_restored_history(tmp_path):
    from scripts.release_mcp_smoke import mcp_scenario
    report = asyncio.run(mcp_scenario(tmp_path, StreamingToolModel))
    assert report['disabled_zero_io'] == 'passed'
    assert report['list_zero_tool_calls'] == 'passed'
    assert report['runtime_add_and_select'] == 'passed'
    assert report['root_reuse_counts'] == [1, 2]
    assert report['child_reuse_counts'] == [1, 2]
    assert report['known_error_and_unknown'] == 'passed'
    assert report['disabled_pending_not_executed'] == 'passed'
    assert report['ttl_rebuild'] == 'passed'
    assert report['cold_history_no_replay'] == 'passed'
    assert report['process_cleanup'] == 'passed'
    assert report['provider_requests'] == 0


def test_mcp_gate_does_not_swallow_cleanup_verification_failure(monkeypatch, tmp_path):
    from scripts import release_mcp_smoke
    monkeypatch.setattr(release_mcp_smoke, 'process_alive', lambda pid: True)
    with pytest.raises(AssertionError, match='fixture process'):
        asyncio.run(release_mcp_smoke.mcp_scenario(tmp_path, StreamingToolModel))
    from test_mcp_connections import process_alive
    records = [json.loads(line) for line in (tmp_path / 'mcp-audit.jsonl').read_text().splitlines()]
    assert all(not process_alive(row['pid']) for row in records if row['event'] == 'start')


def test_installer_rejects_source_only_dependencies_before_reporting_success(monkeypatch, tmp_path):
    import subprocess
    import sys
    from types import SimpleNamespace
    from scripts import verify_wheel_install as check
    wheel = tmp_path / 'candidate.whl'
    wheel.write_bytes(b'candidate')
    output = tmp_path / 'report.json'
    info = {'version': '0.1.1', 'sha256': 'a' * 64}
    monkeypatch.setattr(check, 'inspect_wheel', lambda _: info)
    monkeypatch.setattr(check.venv.EnvBuilder, 'create', lambda self, path: path.mkdir())
    def pip_or_worker(command, **kwargs):
        if command[1:4] == ['-m', 'pip', 'install']:
            # A source-only dependency could compile on a provisioned CI host.
            if '--only-binary=:all:' in command:
                raise subprocess.CalledProcessError(1, command, stderr='No compatible binary dependency')
            report = {'install': [{'metadata': {'name': 'naiyue-test-agent', 'version': info['version']},
                'download_info': {'archive_info': {'hashes': {'sha256': info['sha256']}}}}]}
            from pathlib import Path
            Path(command[command.index('--report') + 1]).write_text(json.dumps(report))
        return SimpleNamespace(returncode=0, stdout='{}', stderr='')
    monkeypatch.setattr(check.subprocess, 'run', pip_or_worker)
    monkeypatch.setattr(sys, 'argv', ['verify_wheel_install', '--wheel', str(wheel), '--output', str(output)])
    with pytest.raises(subprocess.CalledProcessError):
        check.main()
    assert not output.exists()
