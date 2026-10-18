"""Real TCP proves streaming before completion and detachment independent of the producer."""

import os
import subprocess
import sys


def test_web_tcp_disconnect_stop_and_two_process_restore():
    result = subprocess.run([sys.executable, '-m', 'scripts.probe_web', '--timeout-seconds', '30'],
        capture_output=True, text=True, timeout=35,
        env={**os.environ, 'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'})
    assert result.returncode == 0, result.stdout
    assert 'sse_delta=True disconnect_keeps_run=True' in result.stdout
    assert 'two_processes=True history_used=True duplicate_safe=True' in result.stdout


def test_web_tools_tcp_mixed_approval_and_restore():
    result = subprocess.run([sys.executable, '-m', 'scripts.probe_web_tools', '--timeout-seconds', '30'],
        capture_output=True, text=True, timeout=35,
        env={**os.environ, 'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'})
    assert result.returncode == 0, result.stdout
    assert 'approval=correlated mixed_decisions=True resume_run=new duplicate_safe=True' in result.stdout
    assert 'two_processes=True history_used=True' in result.stdout


def test_web_history_tcp_compaction_and_two_process_restore():
    result = subprocess.run([sys.executable, '-m', 'scripts.probe_web_history', '--timeout-seconds', '60'],
        capture_output=True, text=True, timeout=65,
        env={**os.environ, 'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'})
    assert result.returncode == 0, result.stdout
    assert 'tcp=True large_history=True pages=complete compact=preserved' in result.stdout
    assert 'two_processes=True model_context=summary read_replay=False model_calls=20' in result.stdout


def test_web_concurrent_tcp_root_child_approvals_and_archive_reopen():
    result = subprocess.run([sys.executable, '-m', 'scripts.probe_web_concurrency', '--timeout-seconds', '60'],
        capture_output=True, text=True, timeout=65,
        env={**os.environ, 'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'})
    assert result.returncode == 0, result.stdout
    assert 'simultaneous_delta=True disconnect_keeps_both=True controls=isolated' in result.stdout
    assert 'approvals=root_child duplicate_safe=True two_processes=True archive_read_replay=False' in result.stdout
    assert 'model_calls=7 write_commits=1 read_calls=1 provider_requests=0' in result.stdout
