"""Both ownership windows reject restore evidence before reading/creating DBs."""

import os
import sqlite3

import pytest

from naiyue_test_agent.storage.session_storage import SessionStorage, SessionPersistenceError
from naiyue_test_agent.workspaces import prepare_workspace
from tests.storage_backup_fixture import closed_store


@pytest.mark.parametrize('target', ['current', 'missing', 'corrupt'])
@pytest.mark.parametrize('marker', ['invalid', 'file', 'directory', 'symlink'])
def test_sdk_and_workspace_guard_block_before_sqlite_or_file_creation(tmp_path, monkeypatch, target, marker):
    database, root, _ = closed_store(tmp_path)
    if target == 'missing':
        database.unlink()
    elif target == 'corrupt':
        database.write_bytes(b'PRIVATE-corrupt-database')
    before = database.read_bytes() if database.exists() else None
    maintenance = database.with_name(database.name + '.maintenance')
    maintenance.mkdir()
    flag = maintenance / 'restore.json'
    if marker == 'directory':
        flag.mkdir()
    elif marker == 'symlink':
        flag.symlink_to(tmp_path / 'missing-external')
    else:
        flag.write_text('invalid [' if marker == 'invalid' else '{}')
    def forbidden(*args, **kwargs):
        pytest.fail('restore evidence must be checked before SQLite is opened')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    with pytest.raises(SessionPersistenceError, match='restore_incomplete'):
        SessionStorage(database)
    with pytest.raises(ValueError, match='restore_incomplete'):
        prepare_workspace(root, data_dir=tmp_path / 'data', database_path=database)
    assert (database.read_bytes() if database.exists() else None) == before
    assert flag.exists() or flag.is_symlink()


def test_second_guard_covers_marker_created_after_workspace_check(tmp_path):
    database, root, _ = closed_store(tmp_path)
    location = prepare_workspace(root, data_dir=tmp_path / 'data', database_path=database)
    maintenance = database.with_name(database.name + '.maintenance')
    maintenance.mkdir()
    (maintenance / 'restore.json').write_text('{}')
    database.unlink()
    with pytest.raises(SessionPersistenceError, match='restore_incomplete'):
        SessionStorage(location.database_path)
    assert not database.exists()


def test_short_binding_read_holds_the_same_database_lock(tmp_path, monkeypatch):
    from naiyue_test_agent import workspaces
    database, root, _ = closed_store(tmp_path)
    connect, checked = sqlite3.connect, []
    def observe(*args, **kwargs):
        with pytest.raises(SessionPersistenceError, match='storage_in_use'):
            SessionStorage(database)
        checked.append(True)
        return connect(*args, **kwargs)
    monkeypatch.setattr(workspaces.sqlite3, 'connect', observe)
    assert prepare_workspace(root, data_dir=tmp_path / 'data', database_path=database).database_path == database
    assert checked == [True]


def test_workspace_preflight_rejects_database_owned_by_another_process(tmp_path):
    from test_workspace_storage_processes import owner, stop
    root = tmp_path / 'root'
    root.mkdir()
    process, result = owner(root, tmp_path / 'data')
    try:
        with pytest.raises(ValueError, match='storage_in_use'):
            prepare_workspace(root, data_dir=tmp_path / 'data')
        assert process.poll() is None
    finally:
        stop(process)


@pytest.mark.parametrize('unsafe', ['file', 'symlink', 'unreadable'])
def test_untrusted_maintenance_directory_blocks_with_restore_reason(tmp_path, monkeypatch, unsafe):
    from pathlib import Path
    database, root, _ = closed_store(tmp_path)
    directory = database.with_name(database.name + '.maintenance')
    if unsafe == 'file':
        directory.write_text('not a directory')
    elif unsafe == 'symlink':
        directory.symlink_to(tmp_path / 'missing-outside', target_is_directory=True)
    else:
        lstat = Path.lstat
        def denied(path, *args, **kwargs):
            if path == directory:
                raise PermissionError('PRIVATE-maintenance-access')
            return lstat(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'lstat', denied)
    before = database.read_bytes()
    with pytest.raises(SessionPersistenceError, match='restore_incomplete'):
        SessionStorage(database)
    with pytest.raises(ValueError, match='restore_incomplete'):
        prepare_workspace(root, data_dir=tmp_path / 'data', database_path=database)
    assert database.read_bytes() == before
