"""Schema15 is included in the existing validated whole-workspace backup path."""

from contextlib import closing
import sqlite3

import pytest

from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.backup import create_backup, validate_backup, list_backups
from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA, check_snapshot
from tests.storage_backup_fixture import closed_store
from test_memory_readonly import seed


def test_schema14_upgrade_takes_prechange_backup_and_creates_transactional_index(tmp_path):
    database,_,identity=closed_store(tmp_path,version=14)
    with ProjectStore(database) as storage:
        backup,=list_backups(database)
        assert backup['manifest']['source_schema']==14 and backup['manifest']['target_schema']==CURRENT_SCHEMA
        assert validate_backup(database,backup['backup_id'])['source_schema']==14
        item=seed(storage.database.connection,1,workspace_id=identity)
        saved=create_backup(storage.database.connection,database,target_schema=CURRENT_SCHEMA)
    artifact=database.with_name(database.name+'.backups')/saved['backup_id']/'database.sqlite'
    assert check_snapshot(artifact)['source_schema']==CURRENT_SCHEMA
    with closing(sqlite3.connect(artifact)) as connection:
        assert connection.execute('SELECT memory_id FROM naiyue_memories').fetchone()[0]==item
        assert connection.execute('SELECT memory_id FROM naiyue_memory_search').fetchone()[0]==item


def test_memory_migration_failure_keeps_schema14_and_usable_backup(tmp_path,monkeypatch):
    from naiyue_test_agent.application.memory import memory_schema
    from naiyue_test_agent.application.errors import StorageStartupError
    database,_,_=closed_store(tmp_path,version=14)
    error=RuntimeError('private-failure')
    def fail(connection):
        connection.execute('CREATE TABLE partial_memory(value TEXT)')
        raise error
    monkeypatch.setattr(memory_schema,'create',fail)
    with pytest.raises(StorageStartupError) as caught:ProjectStore(database)
    assert caught.value.__cause__ is error
    assert caught.value.current_schema==14 and caught.value.phase=='schema_15'
    assert validate_backup(database,caught.value.backup_id)['source_schema']==14
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0]==14
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='partial_memory'").fetchone() is None


def test_schema15_snapshot_rejects_missing_memory_trigger(tmp_path):
    database,_,_=closed_store(tmp_path)
    # check_snapshot requires a closed standalone file; flush WAL before its immutable read.
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('DROP TRIGGER naiyue_memory_update')
        connection.commit()
    with pytest.raises(ValueError,match='memory_invalid_format'):check_snapshot(database)
