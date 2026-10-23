"""Abrupt process exits at real persistence boundaries, then explicit recovery."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_storage_restore import saved_store, maintenance, invoke, final_result


CHILD = r'''
import json, os, sys
from pathlib import Path
from naiyue_test_agent.storage.restore import restore_backup
from naiyue_test_agent.workspaces import locate_workspace
import naiyue_test_agent.storage.restore_record as records
root, database, backup_id, point = sys.argv[1:]
location = locate_workspace(root, data_dir=None, database_path=database)
rename, replace, unlink, verify = os.rename, os.replace, os.unlink, records.verify_snapshot
def crash():
    os._exit(71)
def renamed(source, target, *args, **kwargs):
    rename(source, target, *args, **kwargs)
    if point == 'install' and Path(source).name == 'candidate.sqlite': crash()
    if point == 'move:' + Path(source).name and Path(target).parent.name == 'original': crash()
def replaced(source, target, *args, **kwargs):
    replace(source, target, *args, **kwargs)
    if Path(target).name == 'restore.json' and json.loads(Path(target).read_text())['phase'] == point: crash()
    if Path(target).name == 'operation.json' and point == 'completed': crash()
def unlinked(path, *args, **kwargs):
    unlink(path, *args, **kwargs)
    if Path(path).name == 'restore.json' and point == 'cleared': crash()
def verified(path, *args, **kwargs):
    value = verify(path, *args, **kwargs)
    if point == 'candidate' and Path(path).name == 'candidate.sqlite': crash()
    return value
os.rename, os.replace, os.unlink, records.verify_snapshot = renamed, replaced, unlinked, verified
restore_backup(location, backup_id, confirm=lambda preview: backup_id)
raise SystemExit('crash boundary not reached')
'''


def crash_restore(location, backup_id, point):
    result = subprocess.run([sys.executable, '-c', CHILD, str(location.root), str(location.database_path),
        backup_id, point], capture_output=True, text=True, timeout=30,
        env={**os.environ, 'PYTHONPATH': str(Path.cwd())})
    assert result.returncode == 71, (result.stdout, result.stderr)


@pytest.mark.parametrize('point', ['candidate', 'prepared', 'move:custom.sqlite', 'move:custom.sqlite-wal',
    'move:custom.sqlite-shm', 'move:custom.sqlite-journal', 'install', 'installed', 'completed', 'cleared'])
def test_process_crash_never_empties_store_and_explicit_retry_finishes(tmp_path, monkeypatch, capsys, point):
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.errors import SessionPersistenceError
    location, saved = saved_store(tmp_path)
    database, backup_id = location.database_path, saved['backup_id']
    original = {database.name + suffix: b'original' + suffix.encode() for suffix in ('', '-wal', '-shm', '-journal')}
    for name, body in original.items():
        database.with_name(name).write_bytes(body)
    crash_restore(location, backup_id, point)
    marker = maintenance(database) / 'restore.json'
    record = json.loads(marker.read_text()) if marker.exists() else None
    if record:
        with pytest.raises(SessionPersistenceError, match='restore_incomplete'):
            ProjectStore(database)
    if point in ('install', 'installed', 'completed'):
        # Closing the already prepared operation does not depend on the source backup.
        database.with_name(database.name + '.backups').rename(tmp_path / 'offline-backups')
    monkeypatch.setattr('builtins.input', lambda prompt: backup_id)
    assert invoke(location, backup_id) == 0
    result = final_result(capsys)
    operation = maintenance(database) / result['operation_id']
    assert not marker.exists()
    if record:
        assert result['operation_id'] == record['operation_id']
        assert {p.name: p.read_bytes() for p in (operation / 'original').iterdir()} == original
    if point == 'candidate':
        assert len(list(maintenance(database).glob('*/candidate.sqlite'))) == 1  # Orphan retained, never trusted.
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 12
        assert connection.execute('SELECT state,run_id FROM naiyue_sessions').fetchone() == ('running', 'R')
