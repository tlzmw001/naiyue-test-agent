"""Project registration validates formats before opening a writable project store."""
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from test_project_registry import service
from test_project_registry_recovery import Crash


def test_existing_foreign_store_is_not_migrated_by_pending_registration(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.storage import database as database_module
    root = tmp_path / 'root'
    root.mkdir()
    original = ProjectRegistry.reserve
    def crash(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(ProjectRegistry, 'reserve', crash)
        with service(tmp_path) as projects:
            with pytest.raises(Crash):
                projects.add_local(root, trusted=True, operation_id='add')
    with sqlite3.connect(tmp_path / 'data/projects.sqlite') as connection:
        target = Path(connection.execute('SELECT database_path FROM projects').fetchone()[0])
    with ProjectStore(target) as store:
        store.sessions.bind_workspace(str(uuid4()), root)
    with sqlite3.connect(target) as connection:
        connection.execute('UPDATE naiyue_store_info SET version=17')
    before = target.read_bytes()
    writable_opens = []
    original_initialize = database_module.initialize_store
    def initialize(connection, path, **kwargs):
        writable_opens.append(path)
        return original_initialize(connection, path, **kwargs)
    monkeypatch.setattr(database_module, 'initialize_store', initialize)
    with service(tmp_path) as projects:
        assert projects.list() == ()
        assert projects.operations()[0].error_code == 'project_identity_conflict'
    assert target.read_bytes() == before
    assert writable_opens == []


def test_registry_open_preserves_primary_error_when_cleanup_also_fails(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.storage.database import Database
    from naiyue_test_agent.storage import registry_database
    from naiyue_test_agent.contracts.projects import ProjectError
    primary = ProjectError('project_registry_invalid_format')
    original = Database.close
    def initialize(database):
        raise primary
    def close(database):
        original(database)
        raise RuntimeError('secondary')
    monkeypatch.setattr(registry_database, 'initialize_registry', initialize)
    monkeypatch.setattr(Database, 'close', close)
    with pytest.raises(ProjectError) as caught:
        Database.open_registry(tmp_path / 'registry.sqlite')
    assert caught.value is primary
    assert 'project_registry_cleanup_secondary_failed' in caplog.text


def test_registry_rejects_other_database_format_without_modification(tmp_path):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.contracts.projects import ProjectError
    target = tmp_path / 'projects.sqlite'
    with sqlite3.connect(target) as connection:
        connection.execute('CREATE TABLE unrelated(content TEXT)')
        connection.execute("INSERT INTO unrelated VALUES('keep')")
    before = target.read_bytes()
    with pytest.raises(ProjectError, match='project_registry_invalid_format'):
        ProjectRegistry(target)
    assert target.read_bytes() == before


def test_registry_ownership_lock_is_released_on_close(tmp_path):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.contracts.projects import ProjectError
    path = tmp_path / 'projects.sqlite'
    first = ProjectRegistry(path)
    try:
        with pytest.raises(ProjectError, match='project_registry_in_use'):
            ProjectRegistry(path)
    finally:
        first.close()
    second = ProjectRegistry(path)
    second.close()


def test_identity_is_checked_on_actual_owned_connection_before_migration(monkeypatch, tmp_path):
    import shutil
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.storage.database import Database
    root = tmp_path / 'code'
    root.mkdir()
    reserve = ProjectRegistry.reserve
    def crash(self, *args, **kwargs):
        result = reserve(self, *args, **kwargs)
        raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(ProjectRegistry, 'reserve', crash)
        with service(tmp_path) as projects:
            with pytest.raises(Crash):
                projects.add_local(root, trusted=True, operation_id='add')
    with sqlite3.connect(tmp_path / 'data/projects.sqlite') as connection:
        identity, raw = connection.execute('SELECT project_id,database_path FROM projects').fetchone()
    target, foreign = Path(raw), tmp_path / 'foreign.sqlite'
    with ProjectStore(target) as store:
        store.sessions.bind_workspace(identity, root)
    with ProjectStore(foreign) as store:
        store.sessions.bind_workspace(str(uuid4()), root)
    with sqlite3.connect(foreign) as connection:
        connection.execute('UPDATE naiyue_store_info SET version=17')
    before = foreign.read_bytes()
    original, swapped = Database.__init__, []
    def replace_on_actual_open(self, path, **kwargs):
        if Path(path) == target and not swapped:
            shutil.copyfile(foreign, target)
            swapped.append(True)
        original(self, path, **kwargs)
    monkeypatch.setattr(Database, '__init__', replace_on_actual_open)
    with service(tmp_path) as projects:
        assert projects.list() == ()
        assert projects.operations()[0].error_code == 'project_identity_conflict'
    assert swapped == [True]
    assert target.read_bytes() == before
