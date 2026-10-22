"""Pre-migration snapshots gate every existing-store version step."""

from contextlib import closing
import sqlite3

import pytest

from naiyue_test_agent.application.errors import SessionPersistenceError
from naiyue_test_agent.application.session_storage import SessionStorage
from naiyue_test_agent.storage_backup import list_backups, validate_backup
from tests.storage_backup_fixture import artifact, closed_store


@pytest.mark.parametrize('version', range(4, 14))
def test_existing_store_is_backed_up_before_any_migration(tmp_path, monkeypatch, version):
    from naiyue_test_agent.application import storage_migrations
    database, root, identity = closed_store(tmp_path, version=version)
    original = storage_migrations.create_backup
    observed = []
    def backup(connection, path, **options):
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == version
        result = original(connection, path, **options)
        assert validate_backup(path, result['backup_id'], workspace_id=identity) == result
        observed.append(result)
        return result
    monkeypatch.setattr(storage_migrations, 'create_backup', backup)
    with SessionStorage(database) as storage:
        assert storage.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 14
        assert storage.get('S')['state'] == 'running'
    assert len(observed) == 1 and observed[0]['source_schema'] == version
    snapshot = artifact(database, observed[0]['backup_id']) / 'database.sqlite'
    with closing(sqlite3.connect(snapshot)) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == version
        assert connection.execute('SELECT state,run_id FROM naiyue_sessions').fetchone() == ('running', 'R')


def test_new_and_current_stores_do_not_backup_but_keep_recovery_initialization(tmp_path, monkeypatch):
    from naiyue_test_agent.application import storage_migrations
    database, _, _ = closed_store(tmp_path)
    def unexpected(*args, **kwargs):
        pytest.fail('new/current stores must not be backed up')
    monkeypatch.setattr(storage_migrations, 'create_backup', unexpected)
    with SessionStorage(database) as storage:
        run = storage.connection.execute('SELECT capture_incomplete,reason FROM naiyue_model_usage_runs').fetchone()
        assert tuple(run) == (1, 'interrupted_execution')
    with SessionStorage(tmp_path / 'new.sqlite') as storage:
        assert storage.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 14
    assert list_backups(database) == []


def test_backup_failure_stops_migrations_releases_lock_and_keeps_original_cause(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application import storage_migrations
    from naiyue_test_agent.application import chat_queries
    database, _, _ = closed_store(tmp_path, version=6)
    original = OSError('PRIVATE-provider-body')
    steps = []
    def denied(*args, **kwargs):
        raise original
    with monkeypatch.context() as patch:
        patch.setattr(storage_migrations, 'create_backup', denied)
        patch.setattr(chat_queries, 'migrate_chat_queries', lambda *_: steps.append('8'))
        with pytest.raises(SessionPersistenceError, match='backup_failed') as caught:
            SessionStorage(database)
    error = caught.value
    assert error.__cause__ is original
    assert (error.source_schema, error.current_schema, error.target_schema, error.phase, error.backup_id) == (6, 6, 14, 'backup', None)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 6
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='naiyue_chat_sync'").fetchone()
    assert steps == [] and 'PRIVATE-provider-body' not in caplog.text
    with SessionStorage(database):
        pass


def test_middle_failure_rolls_back_only_its_step_and_next_start_resumes_committed_version(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application import chat_queries, model_usage_storage
    database, _, _ = closed_store(tmp_path, version=6)
    original = RuntimeError('PRIVATE-migration-failure')
    later = []
    def failing_step(connection):
        with connection:
            connection.execute('BEGIN')
            connection.execute('CREATE TABLE must_rollback(value TEXT)')
            raise original
    with monkeypatch.context() as patch:
        patch.setattr(chat_queries, 'migrate_chat_queries', failing_step)
        patch.setattr(model_usage_storage, 'initialize', lambda *_: later.append(True))
        with pytest.raises(SessionPersistenceError, match='migration_failed') as caught:
            SessionStorage(database)
    error = caught.value
    assert error.__cause__ is original
    assert (error.source_schema, error.current_schema, error.target_schema, error.phase) == (6, 7, 14, 'schema_8')
    assert validate_backup(database, error.backup_id)['source_schema'] == 6
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 7
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='must_rollback'").fetchone() is None
    assert later == [] and 'PRIVATE-migration-failure' not in caplog.text
    with SessionStorage(database):
        pass
    assert sorted(row['manifest']['source_schema'] for row in list_backups(database)) == [6, 7]


def test_version_read_failure_after_commit_keeps_backup_phase_and_primary_error(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application import storage_migrations
    database, _, _ = closed_store(tmp_path, version=6)
    read = storage_migrations.read_schema_version
    original = sqlite3.OperationalError('PRIVATE-version-read')
    def unavailable_after_commit(connection):
        value = read(connection)
        if value == 7:
            raise original
        return value
    with monkeypatch.context() as patch:
        patch.setattr(storage_migrations, 'read_schema_version', unavailable_after_commit)
        with pytest.raises(SessionPersistenceError, match='migration_failed') as caught:
            SessionStorage(database)
    error = caught.value
    assert error.__cause__ is original
    assert (error.source_schema, error.current_schema, error.target_schema, error.phase) == (6, None, 14, 'schema_8')
    assert validate_backup(database, error.backup_id)['source_schema'] == 6
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 7
    assert 'migration_version_unavailable' in caplog.text and 'PRIVATE-version-read' not in caplog.text


@pytest.mark.parametrize('version', [3, 15, 'unknown'])
def test_unknown_version_is_rejected_before_backup_or_changes(tmp_path, monkeypatch, version):
    from naiyue_test_agent.application import storage_migrations
    database, _, _ = closed_store(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('UPDATE naiyue_store_info SET version=?', (version,))
        connection.commit()
    before = database.read_bytes()
    called = []
    monkeypatch.setattr(storage_migrations, 'create_backup', lambda *a, **kw: called.append(True))
    with pytest.raises(SessionPersistenceError, match='unsupported_store_schema'):
        SessionStorage(database)
    assert called == [] and database.read_bytes() == before and list_backups(database) == []


def test_backup_cancel_preserves_interrupt_and_releases_ownership(tmp_path, monkeypatch):
    from naiyue_test_agent.application import storage_migrations
    database, _, _ = closed_store(tmp_path, version=12)
    interrupt = KeyboardInterrupt()
    def cancel(*args, **kwargs):
        raise interrupt
    with monkeypatch.context() as patch:
        patch.setattr(storage_migrations, 'create_backup', cancel)
        with pytest.raises(KeyboardInterrupt) as caught:
            SessionStorage(database)
        assert caught.value is interrupt
    with SessionStorage(database):
        pass


def test_current_version_recovery_failure_is_not_reported_as_migration(tmp_path, monkeypatch):
    from naiyue_test_agent.application import model_usage_storage
    database, _, _ = closed_store(tmp_path)
    original = sqlite3.OperationalError('PRIVATE-recovery-failure')
    def failure(*args, **kwargs):
        raise original
    monkeypatch.setattr(model_usage_storage, 'initialize', failure)
    with pytest.raises(SessionPersistenceError, match='storage_unavailable') as caught:
        SessionStorage(database)
    assert caught.value.__cause__ is original and list_backups(database) == []


@pytest.mark.parametrize('cleanup', ['closed_connection', 'rollback_failure'])
def test_cleanup_failure_keeps_primary_migration_error_and_does_not_guess_current_version(tmp_path, monkeypatch, caplog, cleanup):
    from naiyue_test_agent.application import chat_queries
    database, _, _ = closed_store(tmp_path, version=6)
    original, secondary = RuntimeError('PRIVATE-main'), OSError('PRIVATE-rollback')
    connect = sqlite3.connect
    class BrokenRollback(sqlite3.Connection):
        def rollback(self):
            raise secondary
    def fail(connection):
        if cleanup == 'closed_connection':
            connection.close()
        else:
            connection.execute('BEGIN')
            connection.execute('CREATE TABLE unconfirmed_transaction(value TEXT)')
        raise original
    with monkeypatch.context() as patch:
        if cleanup == 'rollback_failure':
            patch.setattr(sqlite3, 'connect', lambda *a, **kw: connect(*a, **kw, factory=BrokenRollback))
        patch.setattr(chat_queries, 'migrate_chat_queries', fail)
        with pytest.raises(SessionPersistenceError, match='migration_failed') as caught:
            SessionStorage(database)
    assert caught.value.__cause__ is original
    assert caught.value.current_schema is None
    assert validate_backup(database, caught.value.backup_id)['source_schema'] == 6
    with closing(connect(database)) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 7
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='unconfirmed_transaction'").fetchone()
    assert 'migration_rollback_failed' in caplog.text and 'PRIVATE-' not in caplog.text
