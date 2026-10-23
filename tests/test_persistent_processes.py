"""Recovery is proved in separate processes, not only two app objects."""

import subprocess
import sys


def test_two_processes_use_saved_context_and_new_run_ids(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'scripts.probe_persistent_session',
                             '--database', str(tmp_path / 'sessions.sqlite')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout
    assert 'persistent_session_probe=PASS' in result.stdout
    assert 'two_processes=True' in result.stdout and 'history_used=True' in result.stdout


def test_another_process_is_rejected_while_database_is_owned(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    db = tmp_path / 'sessions.sqlite'
    with ProjectStore(db):
        code = '''
import sys
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.application.errors import SessionPersistenceError
try:
    ProjectStore(sys.argv[1])
except SessionPersistenceError:
    print('exclusive_instance=confirmed')
    raise SystemExit(0)
raise SystemExit(1)
'''
        result = subprocess.run([sys.executable, '-c', code, str(db)], capture_output=True,
                                text=True, timeout=15)
        assert result.returncode == 0 and 'exclusive_instance=confirmed' in result.stdout


def test_probe_expired_deadline_does_not_launch_another_worker(monkeypatch, capsys):
    from types import SimpleNamespace
    from scripts import probe_persistent_session as probe
    times = iter([0, 96])
    monkeypatch.setattr(probe, 'time', SimpleNamespace(monotonic=lambda: next(times)))
    monkeypatch.setattr(sys, 'argv', ['probe'])
    def unexpected(*_a, **_k):
        raise AssertionError('expired deadline must not launch a request')
    monkeypatch.setattr(probe.subprocess, 'run', unexpected)
    assert probe.main() == 1
    assert 'deadline_exceeded' in capsys.readouterr().out
