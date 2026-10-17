"""Stable workspace identity follows moves and isolates explicit directory copies."""

import json
from pathlib import Path
import shutil
from uuid import UUID

import pytest


def prepare(root, data, **options):
    from naiyue_test_agent.workspaces import prepare_workspace
    return prepare_workspace(root, data_dir=data, **options)


def test_first_open_creates_stable_manifest_and_uses_data_root(tmp_path, monkeypatch):
    root, data = tmp_path / '项目 A', tmp_path / 'data'
    root.mkdir()
    location = prepare(root, data)
    UUID(location.workspace_id)
    marker = json.loads((root / '.naiyue/workspace.json').read_text())
    assert marker == {'version': 1, 'workspace_id': location.workspace_id}
    assert location.database_path == data / 'workspaces' / location.workspace_id / 'sessions.sqlite'
    assert not location.database_path.exists()
    monkeypatch.chdir(tmp_path)
    assert prepare(root, data) == location
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    assert prepare(alias, data) == location


def test_missing_resume_does_not_initialize_workspace(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    assert prepare(root, tmp_path / 'data', create=False) is None
    assert list(root.iterdir()) == []


def test_workspace_move_updates_binding_and_sessions_without_resetting_state(tmp_path):
    from naiyue_test_agent.application.session_storage import SessionStorage
    root, data = tmp_path / 'A', tmp_path / 'data'
    root.mkdir()
    before = prepare(root, data)
    with SessionStorage(before.database_path) as storage:
        storage.bind_workspace(before.workspace_id, before.root)
        storage.create('S', {'thread_id': 'T', 'model_id': 'M', 'workspace_root': root,
            'active_tool_names': (), 'permission_mode': 'NoAsk'})
        storage.mark_running('S', 'R')
    moved = tmp_path / 'B'
    root.rename(moved)
    after = prepare(moved, data)
    assert after.workspace_id == before.workspace_id
    assert after.database_path == before.database_path
    with SessionStorage(after.database_path) as storage:
        storage.bind_workspace(after.workspace_id, after.root)
        row = storage.get('S')
        assert row['workspace_root'] == str(moved.resolve())
        assert (row['thread_id'], row['model_id'], row['state'], row['run_id']) == ('T', 'M', 'running', 'R')
        assert row['permission_mode'] == 'NoAsk'
        assert storage.get_workspace_binding()['root_path'] == str(moved.resolve())


def test_copy_requires_explicit_new_identity_and_preserves_original(tmp_path):
    from naiyue_test_agent.application.session_storage import SessionStorage
    original, copied, data = tmp_path / 'A', tmp_path / 'B', tmp_path / 'data'
    original.mkdir()
    first = prepare(original, data)
    with SessionStorage(first.database_path) as storage:
        storage.bind_workspace(first.workspace_id, first.root)
    shutil.copytree(original, copied)
    with pytest.raises(ValueError, match='workspace_id_conflict'):
        prepare(copied, data)
    second = prepare(copied, data, new_workspace=True)
    assert second.workspace_id != first.workspace_id
    assert second.database_path != first.database_path
    assert prepare(original, data) == first
    assert prepare(copied, data) == second
    with pytest.raises(ValueError, match='not_a_workspace_copy'):
        prepare(original, data, new_workspace=True)


def test_invalid_manifest_is_not_overwritten(tmp_path):
    root = tmp_path / 'root'
    (root / '.naiyue').mkdir(parents=True)
    marker = root / '.naiyue/workspace.json'
    marker.write_text('{"version":1,"workspace_id":"../../elsewhere"}')
    before = marker.read_bytes()
    with pytest.raises(ValueError, match='invalid_workspace_manifest'):
        prepare(root, tmp_path / 'data')
    assert marker.read_bytes() == before


def test_binding_failure_rolls_back_all_roots(tmp_path):
    from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
    root = tmp_path / 'A'
    root.mkdir()
    loc = prepare(root, tmp_path / 'data')
    with SessionStorage(loc.database_path) as storage:
        storage.bind_workspace(loc.workspace_id, root)
        storage.create('S', {'thread_id': 'T', 'model_id': 'M', 'workspace_root': root,
            'active_tool_names': (), 'permission_mode': 'Default'})
        storage.connection.execute("CREATE TRIGGER reject_root BEFORE UPDATE OF workspace_root ON naiyue_sessions BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        storage.connection.commit()
    moved = tmp_path / 'B'
    root.rename(moved)
    with SessionStorage(loc.database_path) as storage:
        with pytest.raises(SessionPersistenceError):
            storage.bind_workspace(loc.workspace_id, moved)
        assert storage.get_workspace_binding()['root_path'] == str(root)
        assert storage.get('S')['workspace_root'] == str(root)


def test_storage_root_setting_is_absolute_and_does_not_silently_fallback(tmp_path):
    from naiyue_test_agent.tools.settings import load_tool_settings
    config = tmp_path / 'settings.toml'
    data = tmp_path / 'another-drive'
    config.write_text('[storage]\ndata_dir=' + json.dumps(str(data)) + '\n')
    assert load_tool_settings(config).storage.data_dir == data
    config.write_text('[storage]\ndata_dir="relative/data"\n')
    with pytest.raises(ValueError):
        load_tool_settings(config)


def test_unavailable_data_root_does_not_fallback(tmp_path):
    from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
    root, data = tmp_path / 'A', tmp_path / 'blocked'
    root.mkdir()
    data.write_text('not a directory')
    loc = prepare(root, data)
    with pytest.raises(SessionPersistenceError):
        SessionStorage(loc.database_path)
    assert data.read_text() == 'not a directory'


def test_atomic_manifest_failure_does_not_publish_or_leak_lock(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent import workspaces
    root = tmp_path / 'root'
    root.mkdir()
    original_replace = workspaces.os.replace
    def fail(*_args):
        raise OSError('synthetic_replace_failure')
    monkeypatch.setattr(workspaces.os, 'replace', fail)
    with pytest.raises(OSError):
        prepare(root, tmp_path / 'data')
    assert not (root / '.naiyue/workspace.json').exists()
    assert list((root / '.naiyue').glob('.workspace-*.tmp')) == []
    assert 'workspace_prepare_failed' in caplog.text
    monkeypatch.setattr(workspaces.os, 'replace', original_replace)
    assert prepare(root, tmp_path / 'data').workspace_id


def test_existing_marker_empty_resume_performs_no_write(tmp_path, monkeypatch):
    from naiyue_test_agent import workspaces
    root = tmp_path / 'root'
    root.mkdir()
    location = prepare(root, tmp_path / 'data')
    (root / '.naiyue/workspace.lock').unlink()
    monkeypatch.setattr(workspaces, 'open_store_file', lambda *_: pytest.fail('Read-only resume must not open a lock'))
    assert prepare(root, tmp_path / 'data', create=False) == location
    assert not (root / '.naiyue/workspace.lock').exists()


def test_schema_three_is_rejected_without_changes(tmp_path):
    import sqlite3
    from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
    db = tmp_path / 'old.sqlite'
    with sqlite3.connect(db) as connection:
        connection.executescript('CREATE TABLE naiyue_store_info(version INTEGER); INSERT INTO naiyue_store_info VALUES(3);')
    before = db.read_bytes()
    with pytest.raises(SessionPersistenceError, match='unsupported_store_schema'):
        SessionStorage(db)
    assert db.read_bytes() == before


def test_marker_failure_remains_primary_when_lock_cleanup_also_fails(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent import workspaces
    root = tmp_path / 'root'
    root.mkdir()
    original = OSError('synthetic_marker_failure')
    def fail(*args):
        raise original
    release = workspaces.release_store_lock
    def cleanup(fd):
        release(fd)
        raise OSError('synthetic_cleanup_failure')
    monkeypatch.setattr(workspaces.os, 'replace', fail)
    monkeypatch.setattr(workspaces, 'release_store_lock', cleanup)
    with pytest.raises(OSError) as caught:
        prepare(root, tmp_path / 'data')
    assert caught.value is original
    assert 'workspace_lock_cleanup_failed' in caplog.text
    assert not (root / '.naiyue/workspace.json').exists()
