"""Only complete, strictly parsed, standalone backups are usable."""

from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3

import pytest

from tests.storage_backup_fixture import artifact, source_store


@pytest.mark.parametrize('operation', ['backup', 'file_facts', 'restore_snapshot'])
def test_descriptor_change_time_can_differ_from_path_birth_time(tmp_path, monkeypatch, operation):
    from types import SimpleNamespace
    from naiyue_test_agent import storage_backup
    source, root, _ = source_store(tmp_path)
    with closing(source):
        manifest = storage_backup.create_backup(source.connection, source.path, target_schema=14)
        snapshot = artifact(source.path, manifest['backup_id']) / 'database.sqlite'
        identity = snapshot.stat().st_ino
        fstat = storage_backup.os.fstat
        def descriptor_stat(fd):
            info = fstat(fd)
            if info.st_ino != identity:
                return info
            fields = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
            fields['st_ctime_ns'] += 1_000_000_000
            return SimpleNamespace(**fields)
        # CPython 3.12 Windows fstat reports change time, lstat preserves birth time.
        monkeypatch.setattr(storage_backup.os, 'fstat', descriptor_stat)
        if operation == 'backup':
            assert storage_backup.validate_backup(source.path, manifest['backup_id']) == manifest
        elif operation == 'file_facts':
            from naiyue_test_agent.storage_restore_record import file_facts
            assert file_facts(snapshot)['sha256'] == manifest['sha256']
        else:
            from naiyue_test_agent.storage_restore_record import verify_snapshot
            from naiyue_test_agent.workspaces import locate_workspace
            location = locate_workspace(root, data_dir=None, database_path=source.path)
            verify_snapshot(snapshot, {'source_schema': manifest['source_schema'],
                'candidate_bytes': manifest['database_bytes'], 'candidate_sha256': manifest['sha256']}, location)


@pytest.mark.parametrize('fault', ['manifest', 'identity'])
def test_invalid_backup_reports_only_safe_validation_stage(tmp_path, monkeypatch, caplog, fault):
    from naiyue_test_agent import storage_backup
    with closing(source_store(tmp_path)[0]) as source:
        manifest = storage_backup.create_backup(source.connection, source.path, target_schema=14)
        directory = artifact(source.path, manifest['backup_id'])
        if fault == 'manifest':
            (directory / 'manifest.json').write_text('{"PRIVATE-token":1}')
        else:
            check = storage_backup.check_snapshot
            def changed(path):
                facts = check(path)
                info = path.stat()
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
                return facts
            monkeypatch.setattr(storage_backup, 'check_snapshot', changed)
        caplog.clear()
        with pytest.raises(ValueError, match='backup_invalid'):
            storage_backup.validate_backup(source.path, manifest['backup_id'])
        assert f'stage={fault}' in caplog.text
        if fault == 'identity':
            assert 'st_mtime_ns' in caplog.text
        assert 'PRIVATE-token' not in caplog.text
        assert str(directory) not in caplog.text


@pytest.mark.parametrize('fault', ['duplicate', 'oversized', 'future_format', 'bool', 'extra',
    'id', 'date', 'naive_date', 'reason', 'filename', 'windows_filename', 'size', 'hash',
    'workspace', 'version', 'missing', 'symlink', 'hardlink', 'directory', 'sidecar',
    'snapshot_structure', 'snapshot_identity', 'corruption'])
def test_invalid_backup_is_visible_and_never_accepted(tmp_path, caplog, fault):
    from naiyue_test_agent.storage_backup import create_backup, validate_backup, list_backups
    with closing(source_store(tmp_path)[0]) as source:
        manifest = create_backup(source.connection, source.path, target_schema=14)
        backup_id = manifest['backup_id']
        directory = artifact(source.path, backup_id)
        info, snapshot = directory / 'manifest.json', directory / 'database.sqlite'
        changes = {'future_format': ('format_version', 2), 'bool': ('source_schema', True),
            'extra': ('PRIVATE-token', 'PRIVATE-token'), 'id': ('backup_id', '0' * 32),
            'date': ('created_at', 'PRIVATE-token'), 'naive_date': ('created_at', '2026-01-01T00:00:00'),
            'reason': ('reason', 'other'), 'filename': ('database_name', '../other'),
            'windows_filename': ('database_name', 'C:\\other'), 'size': ('database_bytes', 1),
            'hash': ('sha256', '0' * 64), 'workspace': ('workspace_id', None), 'version': ('source_schema', 12)}
        if fault in changes:
            key, value = changes[fault]
            manifest[key] = value
            info.write_text(json.dumps(manifest))
        elif fault == 'duplicate':
            info.write_text(info.read_text().replace('{', '{"format_version":1,', 1))
        elif fault == 'oversized':
            info.write_bytes(b' ' * 65537)
        elif fault == 'missing':
            info.unlink()
        elif fault in ('symlink', 'hardlink', 'directory'):
            external = tmp_path / 'external'
            snapshot.rename(external)
            if fault == 'symlink':
                snapshot.symlink_to(external)
            elif fault == 'hardlink':
                os.link(external, snapshot)
            else:
                snapshot.mkdir()
        elif fault == 'sidecar':
            (directory / 'database.sqlite-wal').write_bytes(b'unsafe')
        else:
            if fault == 'corruption':
                snapshot.write_bytes(b'PRIVATE-token-bad-sqlite')
            else:
                with closing(sqlite3.connect(snapshot)) as connection:
                    connection.execute('DROP TABLE naiyue_diagnostic_runs' if fault == 'snapshot_structure'
                        else "UPDATE naiyue_workspace SET workspace_id='invalid'")
                    connection.commit()
            manifest['database_bytes'] = snapshot.stat().st_size
            manifest['sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            info.write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match='backup_invalid'):
            validate_backup(source.path, backup_id)
        rows = list_backups(source.path)
        assert rows == [{'backup_id': backup_id, 'status': 'invalid', 'error': 'backup_invalid'}]
        assert 'PRIVATE-token' not in caplog.text


@pytest.mark.parametrize('identity', ['../outside', '/tmp/path', 'A' * 32, '.', '', '0' * 33])
def test_id_is_not_a_path(tmp_path, identity):
    from naiyue_test_agent.storage_backup import validate_backup
    with pytest.raises(ValueError, match='backup_invalid'):
        validate_backup(tmp_path / 'db', identity)


def test_higher_planned_target_is_diagnostic_and_listing_is_read_only_sorted(tmp_path):
    from naiyue_test_agent.storage_backup import create_backup, validate_backup, list_backups
    with closing(source_store(tmp_path)[0]) as source:
        first = create_backup(source.connection, source.path, target_schema=14)
        second = create_backup(source.connection, source.path, target_schema=14)
        root = artifact(source.path, '')
        for value, stamp in ((first, '2026-01-01T00:00:00+00:00'), (second, '2026-01-02T00:00:00+00:00')):
            value['created_at'], value['target_schema'] = stamp, 999
            (root / value['backup_id'] / 'manifest.json').write_text(json.dumps(value))
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
        assert validate_backup(source.path, first['backup_id']) == first
        rows = list_backups(source.path)
        assert [r['backup_id'] for r in rows] == [second['backup_id'], first['backup_id']]
        assert all(r['status'] == 'valid' for r in rows)
        assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
        (root / ('.pending-' + 'f' * 32)).mkdir()
        (root / 'unknown\nPRIVATE').write_text('untrusted')
        rows = list_backups(source.path)
        assert {r['status'] for r in rows} == {'valid', 'invalid', 'incomplete'}
        assert all('\n' not in r['backup_id'] for r in rows)


def test_deeply_nested_manifest_does_not_hide_healthy_backups(tmp_path, caplog):
    from naiyue_test_agent.storage_backup import create_backup, validate_backup, list_backups
    with closing(source_store(tmp_path)[0]) as source:
        good = create_backup(source.connection, source.path, target_schema=14)
        bad = create_backup(source.connection, source.path, target_schema=14)
        (artifact(source.path, bad['backup_id']) / 'manifest.json').write_text('[' * 20000 + '0' + ']' * 20000)
        with pytest.raises(ValueError, match='backup_invalid'):
            validate_backup(source.path, bad['backup_id'])
        rows = list_backups(source.path)
        assert {row['backup_id']: row['status'] for row in rows} == {
            good['backup_id']: 'valid', bad['backup_id']: 'invalid'}
        assert 'error_type=RecursionError' in caplog.text


@pytest.mark.parametrize('fault', ['other_database', 'other_workspace', 'missing_checkpoint_table',
    'missing_writes_column', 'duplicate_version', 'foreign_key'])
def test_snapshot_ownership_and_structure_cannot_be_replaced_by_matching_hash(tmp_path, fault):
    from naiyue_test_agent.storage_backup import create_backup, validate_backup
    with closing(source_store(tmp_path)[0]) as source:
        saved = create_backup(source.connection, source.path, target_schema=14)
        target, expected = source.path, saved['workspace_id']
        if fault == 'other_database':
            target = source.path.with_name('other.sqlite')
            shutil.copytree(artifact(source.path, ''), artifact(target, ''))
        elif fault == 'other_workspace':
            expected = '00000000-0000-0000-0000-000000000000'
        else:
            directory = artifact(source.path, saved['backup_id'])
            snapshot = directory / 'database.sqlite'
            with closing(sqlite3.connect(snapshot)) as connection:
                if fault == 'foreign_key':
                    connection.executescript('CREATE TABLE parent(id INTEGER PRIMARY KEY); '
                        'CREATE TABLE child(id INTEGER REFERENCES parent(id)); INSERT INTO child VALUES(99);')
                else:
                    connection.execute({'missing_checkpoint_table': 'DROP TABLE checkpoints',
                        'missing_writes_column': 'ALTER TABLE writes DROP COLUMN value',
                        'duplicate_version': 'INSERT INTO naiyue_store_info VALUES(13)'}[fault])
                connection.commit()
            saved['database_bytes'] = snapshot.stat().st_size
            saved['sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            (directory / 'manifest.json').write_text(json.dumps(saved))
        with pytest.raises(ValueError, match='backup_invalid'):
            validate_backup(target, saved['backup_id'], workspace_id=expected)
