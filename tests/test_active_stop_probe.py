"""Offline process probe exercises the exact active-stop verification entry."""

from pathlib import Path
import subprocess
import sys


def test_active_stop_probe_offline():
    result = subprocess.run([sys.executable, '-m', 'scripts.probe_active_stop', '--offline'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == ('active_stop_probe=PASS mode=offline logical_calls=1 '
        'delta=observed stopped=verified fake_answer=absent checkpoint=safe')
