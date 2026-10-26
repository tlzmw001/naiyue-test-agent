"""Fail-closed restore records, original groups, and secondary IO failures."""

from contextlib import closing
import json
import os
import sqlite3
from uuid import uuid4

import pytest

from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA

from tests.storage_backup_fixture import artifact
from tests.test_storage_restore import saved_store, maintenance, invoke, final_result
from tests.test_storage_restore_processes import crash_restore


@pytest.mark.parametrize('version', range(4, CURRENT_SCHEMA))
def test_each_supported_source_restores_without_running_its_migrations(tmp_path, monkeypatch, capsys, version):
    location, saved = saved_store(tmp_path, version=version)
    monkeypatch.setattr('builtins.input', lambda prompt: saved['backup_id'])
    assert invoke(location, saved['backup_id']) == 0
    assert final_result(capsys)['status'] == 'completed'
    with closing(sqlite3.connect(location.database_path)) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == version
        assert connection.execute('SELECT state,run_id FROM naiyue_sessions').fetchone() == ('running', 'R')


@pytest.mark.parametrize('change', ['invalid-json', 'duplicate', 'too-large', 'deep', 'extra', 'id', 'workspace',
    'phase', 'schema', 'bytes', 'filename', 'duplicate-file', 'digest', 'bool'])
def test_untrusted_active_record_never_moves_or_opens_the_target(tmp_path, monkeypatch, capsys, caplog, change):
    location, saved = saved_store(tmp_path)
    database = location.database_path
    crash_restore(location, saved['backup_id'], 'prepared')
    marker = maintenance(database) / 'restore.json'
    record = json.loads(marker.read_text())
    before = database.read_bytes()
    if change == 'extra': record['PRIVATE-token'] = 'secret'
    elif change == 'id': record['operation_id'] = '../outside'
    elif change == 'workspace': record['workspace_id'] = str(uuid4())
    elif change == 'phase': record['phase'] = 'completed'
    elif change == 'schema': record['source_schema'] = 999
    elif change == 'bytes': record['candidate_bytes'] = -1
    elif change == 'filename': record['original_files'][0]['filename'] = '../PRIVATE-token'
    elif change == 'duplicate-file': record['original_files'].append(record['original_files'][0])
    elif change == 'digest': record['candidate_sha256'] = 'bad'
    elif change == 'bool': record['format_version'] = True
    body = json.dumps(record)
    if change == 'invalid-json': body = 'PRIVATE-token['
    elif change == 'duplicate': body = body[:-1] + ', "phase": "prepared"}'
    elif change == 'too-large': body = ' ' * 65537
    elif change == 'deep': body = '[' * 2000 + '0' + ']' * 2000
    marker.write_text(body)
    def forbidden(*args, **kwargs):
        raise AssertionError('untrusted record must fail before SQLite and confirmation')
    monkeypatch.setattr('builtins.input', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    assert invoke(location, saved['backup_id']) == 1
    assert final_result(capsys)['error'] == 'restore_conflict'
    assert database.read_bytes() == before and marker.read_text() == body
    assert 'PRIVATE-token' not in caplog.text


@pytest.mark.parametrize('change', ['missing-original', 'both-original', 'changed-original', 'extra-original',
    'extra-active', 'changed-candidate', 'duplicate-candidate', 'completed-conflict', 'different-backup'])
def test_conflicting_file_state_is_retained_and_never_guessed(tmp_path, monkeypatch, capsys, change):
    location, saved = saved_store(tmp_path)
    database = location.database_path
    point = 'installed' if change in ('duplicate-candidate', 'completed-conflict') else 'prepared'
    crash_restore(location, saved['backup_id'], point)
    marker = maintenance(database) / 'restore.json'
    record = json.loads(marker.read_text())
    operation = maintenance(database) / record['operation_id']
    if change == 'missing-original': database.unlink()
    elif change == 'both-original': (operation / 'original' / database.name).write_bytes(database.read_bytes())
    elif change == 'changed-original': database.write_bytes(b'changed')
    elif change == 'extra-original': (operation / 'original' / 'unknown').write_bytes(b'extra')
    elif change == 'extra-active': database.with_name(database.name + '-wal').write_bytes(b'extra')
    elif change == 'changed-candidate': (operation / 'candidate.sqlite').write_bytes(b'bad')
    elif change == 'duplicate-candidate': (operation / 'candidate.sqlite').write_bytes(database.read_bytes())
    elif change == 'completed-conflict': (operation / 'operation.json').write_text(json.dumps({**record, 'phase': 'completed', 'backup_id': uuid4().hex}))
    def forbidden(prompt):
        raise AssertionError('must reject conflicts before asking')
    monkeypatch.setattr('builtins.input', forbidden)
    backup_id = uuid4().hex if change == 'different-backup' else saved['backup_id']
    assert invoke(location, backup_id) == 1
    assert final_result(capsys)['error'] == 'restore_conflict'
    assert json.loads(marker.read_text()) == record


@pytest.mark.parametrize('kind', ['symlink-target', 'hardlink-target', 'symlink-candidate', 'hardlink-marker', 'symlink-original'])
def test_unsafe_files_cannot_be_used_or_moved(tmp_path, monkeypatch, capsys, kind):
    location, saved = saved_store(tmp_path)
    database = location.database_path
    outside = tmp_path / 'outside'
    outside.write_bytes(b'outside-private')
    if kind.endswith('target'):
        target = database
    else:
        crash_restore(location, saved['backup_id'], 'prepared')
        record = json.loads((maintenance(database) / 'restore.json').read_text())
        operation = maintenance(database) / record['operation_id']
        target = (operation / 'candidate.sqlite' if kind == 'symlink-candidate' else
                  maintenance(database) / 'restore.json' if kind == 'hardlink-marker' else operation / 'original')
    if target.is_dir(): target.rmdir()
    else: target.unlink()
    if kind.startswith('hardlink'): os.link(outside, target)
    else: target.symlink_to(outside)
    monkeypatch.setattr('builtins.input', lambda prompt: saved['backup_id'])
    assert invoke(location, saved['backup_id']) == 1
    assert final_result(capsys)['error'] in ('restore_failed', 'restore_conflict')
    assert outside.read_bytes() == b'outside-private'
    assert target.is_symlink() if kind.startswith('symlink') else target.stat().st_nlink == 2


@pytest.mark.parametrize('point', ['candidate', 'marker', 'move', 'install', 'completion', 'unlink'])
def test_io_failure_preserves_primary_error_and_all_recovery_evidence(tmp_path, monkeypatch, caplog, point):
    from naiyue_test_agent.storage.restore import restore_backup
    from naiyue_test_agent.storage import restore as executor, restore_record as records
    location, saved = saved_store(tmp_path)
    database = location.database_path
    original = database.read_bytes()
    failure = OSError('PRIVATE-token body')
    create, write, rename, unlink = executor.open_store_file, records.write_record, os.rename, os.unlink
    def create_file(path, **kwargs):
        if point == 'candidate' and path.name == 'candidate.sqlite': raise failure
        return create(path, **kwargs)
    def write_record(path, value):
        if (point == 'marker' and path.name == 'restore.json' and value['phase'] == 'prepared'
                or point == 'completion' and path.name == 'operation.json'): raise failure
        return write(path, value)
    def move(source, target):
        if point == 'move' and target.parent.name == 'original' or point == 'install' and source.name == 'candidate.sqlite': raise failure
        return rename(source, target)
    def remove(path):
        if point == 'unlink' and path.name == 'restore.json': raise failure
        return unlink(path)
    with monkeypatch.context() as patch:
        patch.setattr(executor, 'open_store_file', create_file)
        patch.setattr(records, 'write_record', write_record)
        patch.setattr(os, 'rename', move)
        patch.setattr(os, 'unlink', remove)
        def cleanup(fd):
            executor.release_store_lock_original(fd)
            raise OSError('SECONDARY-private')
        patch.setattr(executor, 'release_store_lock_original', executor.release_store_lock, raising=False)
        patch.setattr(executor, 'release_store_lock', cleanup)
        with pytest.raises(ValueError, match='restore_failed') as caught:
            restore_backup(location, saved['backup_id'], confirm=lambda preview: saved['backup_id'])
        assert caught.value.__cause__ is failure
    assert 'PRIVATE-token' not in caplog.text and 'SECONDARY-private' not in caplog.text
    assert 'storage_restore_lock_cleanup_failed' in caplog.text
    marker = maintenance(database) / 'restore.json'
    if point in ('candidate', 'marker'):
        assert database.read_bytes() == original and not marker.exists()
    else:
        assert marker.exists()
        result = restore_backup(location, saved['backup_id'], confirm=lambda preview: saved['backup_id'])
        assert result['status'] == 'completed' and not marker.exists()
