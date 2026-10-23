"""Snapshots preserve WAL and opaque state without running migrations or Agents."""

from contextlib import closing
import hashlib
import json
import os
import sqlite3

import pytest

from naiyue_test_agent.storage_backup_schema import CURRENT_SCHEMA

from tests.storage_backup_fixture import artifact, source_store


@pytest.mark.parametrize('version', range(4, CURRENT_SCHEMA + 1))
def test_snapshot_covers_wal_and_source_schema_without_migration(tmp_path, version):
    from naiyue_test_agent.storage_backup import create_backup, validate_backup
    with closing(source_store(tmp_path, version=version)[0]) as source:
        connection, database = source.connection, source.path
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute("UPDATE naiyue_sessions SET model_id='committed-in-wal'")
        connection.commit()
        assert database.with_name(database.name + '-wal').stat().st_size > 0
        before = connection.total_changes
        manifest = create_backup(connection, database, target_schema=CURRENT_SCHEMA)
        assert connection.total_changes == before
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == version
        assert (manifest['source_schema'], manifest['target_schema']) == (version, CURRENT_SCHEMA)
        directory = artifact(database, manifest['backup_id'])
        assert sorted(p.name for p in directory.iterdir()) == ['database.sqlite', 'manifest.json']
        assert validate_backup(database, manifest['backup_id'], workspace_id=manifest['workspace_id']) == manifest
        snapshot = directory / 'database.sqlite'
        assert manifest['sha256'] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
        with closing(sqlite3.connect(snapshot.as_uri() + '?mode=ro&immutable=1', uri=True)) as saved:
            assert saved.execute('SELECT model_id,state,run_id FROM naiyue_sessions').fetchone() == ('committed-in-wal', 'running', 'R')
            assert saved.execute('SELECT checkpoint,parent_checkpoint_id FROM checkpoints').fetchone() == (b'opaque-checkpoint', 'pruned-parent')
            assert saved.execute('SELECT value FROM writes').fetchone() == (b'opaque-value',)


def test_unbound_and_never_run_store_is_valid_without_fabricated_identity(tmp_path):
    from naiyue_test_agent.storage_backup import create_backup, validate_backup
    with closing(source_store(tmp_path, bound=False, checkpoints=False)[0]) as source:
        saved = create_backup(source.connection, source.path, target_schema=CURRENT_SCHEMA)
        assert saved['workspace_id'] is None
        assert validate_backup(source.path, saved['backup_id']) == saved
        with pytest.raises(ValueError, match='backup_invalid'):
            validate_backup(source.path, saved['backup_id'], workspace_id='other')


@pytest.mark.parametrize('fault', ['transaction', 'wrong_source', 'schema', 'binding', 'missing_table', 'checkpoint'])
def test_generation_refuses_invalid_source_and_does_not_publish(tmp_path, fault):
    from naiyue_test_agent.storage_backup import create_backup, list_backups
    with closing(source_store(tmp_path)[0]) as source:
        connection, database = source.connection, source.path
        if fault == 'transaction':
            connection.execute('BEGIN')
        elif fault == 'wrong_source':
            database = tmp_path / 'different.sqlite'
        else:
            sql = {'schema': 'UPDATE naiyue_store_info SET version=999',
                'binding': "UPDATE naiyue_workspace SET workspace_id='invalid'",
                'missing_table': 'DROP TABLE naiyue_diagnostic_events',
                'checkpoint': "UPDATE naiyue_sessions SET checkpoint_id='missing'"}[fault]
            connection.execute(sql)
            connection.commit()
        with pytest.raises(ValueError, match='backup_failed'):
            create_backup(connection, database, target_schema=CURRENT_SCHEMA)
        assert all(row['status'] != 'valid' for row in list_backups(database))


@pytest.mark.parametrize('stage', ['backup', 'manifest', 'publish', 'cancel'])
def test_generation_io_failure_preserves_pending_and_primary_error(tmp_path, monkeypatch, caplog, stage):
    from naiyue_test_agent import storage_backup as module
    with closing(source_store(tmp_path)[0]) as source:
        error = KeyboardInterrupt() if stage == 'cancel' else OSError('PRIVATE-token-body')
        def fail(*args, **kwargs):
            raise error
        if stage in ('backup', 'cancel'):
            monkeypatch.setattr(module, 'check_snapshot', fail)
        elif stage == 'manifest':
            monkeypatch.setattr(module.json, 'dump', fail)
        else:
            monkeypatch.setattr(module.os, 'rename', fail)
        with pytest.raises(KeyboardInterrupt if stage == 'cancel' else ValueError) as caught:
            module.create_backup(source.connection, source.path, target_schema=CURRENT_SCHEMA)
        if stage != 'cancel':
            assert caught.value.__cause__ is error
        rows = module.list_backups(source.path)
        assert len(rows) == 1 and rows[0]['status'] == 'incomplete'
        assert source.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == CURRENT_SCHEMA
        assert 'PRIVATE-token-body' not in caplog.text


def test_backup_root_symlink_is_refused_without_writing_other_directory(tmp_path):
    from naiyue_test_agent.storage_backup import create_backup, list_backups
    with closing(source_store(tmp_path)[0]) as source:
        other = tmp_path / 'outside'
        other.mkdir()
        artifact(source.path, '').symlink_to(other, target_is_directory=True)
        with pytest.raises(ValueError, match='backup_failed'):
            create_backup(source.connection, source.path, target_schema=CURRENT_SCHEMA)
        with pytest.raises(ValueError, match='backup_invalid'):
            list_backups(source.path)
        assert list(other.iterdir()) == []
