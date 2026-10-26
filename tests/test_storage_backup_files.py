"""File safety and cleanup failures use real files or exact platform dispatch."""

from contextlib import closing
import ctypes
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA

from tests.storage_backup_fixture import artifact, source_store


def test_destination_cleanup_keeps_original_backup_failure(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.storage import backup as storage_backup
    original, secondary = OSError('PRIVATE-original'), OSError('PRIVATE-close')
    connect = sqlite3.connect
    class FailedDestination(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == 'PRAGMA journal_mode=DELETE':
                raise original
            return super().execute(sql, *args)

        def close(self):
            super().close()
            raise secondary
    with closing(source_store(tmp_path)[0]) as source:
        monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: connect(*a, **kw, factory=FailedDestination))
        with pytest.raises(ValueError, match='backup_failed') as caught:
            storage_backup.create_backup(source.connection, source.path, target_schema=CURRENT_SCHEMA)
        assert caught.value.__cause__ is original
        assert 'backup_connection_cleanup_failed' in caplog.text
        assert 'PRIVATE-' not in caplog.text


def test_snapshot_flush_uses_writable_handle(tmp_path, monkeypatch):
    from naiyue_test_agent.storage import backup as storage_backup
    sync = os.fsync
    observed = []
    def flush(fd):
        # _commit/FlushFileBuffers on Windows also needs GENERIC_WRITE.
        os.write(fd, b'')
        observed.append(fd)
        return sync(fd)
    with closing(source_store(tmp_path)[0]) as source:
        monkeypatch.setattr(storage_backup.os, 'fsync', flush)
        # Directory fds cannot be written; directory flushing has a separate contract.
        monkeypatch.setattr(storage_backup, 'sync_store_directory', lambda path: None)
        storage_backup.create_backup(source.connection, source.path, target_schema=CURRENT_SCHEMA)
        assert len(observed) == 2


def test_readonly_handles_never_create_change_or_follow_files(tmp_path):
    from naiyue_test_agent.storage.files import open_store_readonly
    path = tmp_path / 'file'
    with pytest.raises(FileNotFoundError):
        open_store_readonly(path)
    assert not path.exists()
    path.write_bytes(b'untouched')
    path.chmod(0o644)
    fd = open_store_readonly(path)
    try:
        assert not os.get_inheritable(fd)
        assert os.read(fd, 20) == b'untouched'
        with pytest.raises(OSError):
            os.write(fd, b'no')
    finally:
        os.close(fd)
    alias = tmp_path / 'link'
    alias.symlink_to(path)
    with pytest.raises(OSError):
        open_store_readonly(alias)
    assert path.read_bytes() == b'untouched'


def test_windows_readonly_open_uses_existing_and_read_access(monkeypatch, tmp_path):
    from naiyue_test_agent.storage import files as storage_files
    calls = []
    def create(*args):
        calls.append(args)
        return 456
    def information(handle, pointer):
        pointer._obj.attributes, pointer._obj.links = 0, 1
        return True
    kernel = SimpleNamespace(CreateFileW=create, GetFileInformationByHandle=information,
                             CloseHandle=lambda handle: calls.append(('close', handle)))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: kernel, raising=False)
    monkeypatch.setattr(storage_files, 'os', SimpleNamespace(O_RDONLY=0, O_RDWR=2, O_BINARY=0x8000, O_NOINHERIT=0x80))
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(open_osfhandle=lambda handle, flags: calls.append((handle, flags)) or 9))
    assert storage_files._windows_open(tmp_path / 'file', False, readonly=True) == 9
    assert calls[0][1] == 0x80000000 and calls[0][4] == 3  # GENERIC_READ / OPEN_EXISTING.
    assert calls[0][5] & 0x00200000
    assert calls[1] == (456, 0x8000 | 0x80)


@pytest.mark.parametrize('kind', ['manifest_link', 'backup_link', 'parent_link', *(['fifo'] if os.name != 'nt' else [])])
def test_unsafe_backup_entries_are_invalid_without_traversal(tmp_path, kind):
    from naiyue_test_agent.storage.backup import create_backup, validate_backup
    with closing(source_store(tmp_path)[0]) as source:
        saved = create_backup(source.connection, source.path, target_schema=CURRENT_SCHEMA)
        directory = artifact(source.path, saved['backup_id'])
        if kind == 'manifest_link':
            info = directory / 'manifest.json'
            external = tmp_path / 'manifest'
            info.rename(external)
            info.symlink_to(external)
        elif kind == 'backup_link':
            external = tmp_path / 'backup'
            directory.rename(external)
            directory.symlink_to(external, target_is_directory=True)
        elif kind == 'parent_link':
            directory = artifact(source.path, '')
            external = tmp_path / 'backups'
            directory.rename(external)
            directory.symlink_to(external, target_is_directory=True)
        else:
            snapshot = directory / 'database.sqlite'
            snapshot.unlink()
            os.mkfifo(snapshot)
        with pytest.raises(ValueError, match='backup_invalid'):
            validate_backup(source.path, saved['backup_id'])
