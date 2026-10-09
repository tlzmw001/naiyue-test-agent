"""Two isolated Python processes must continue an async saved conversation."""

import subprocess
import sys
from pathlib import Path


def test_async_process_probe_offline():
    result = subprocess.run([sys.executable, '-m', 'scripts.probe_async_application', '--offline'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == (
        'async_application_probe=PASS mode=offline two_processes=True delta=verified tool=1 '
        'steering=verified terminal_after_save=verified history_used=True '
        'session_id=stable thread_id=stable run_id=new checkpoint=advanced')
