"""Reproductions for the independent R1-03 architecture and security review."""
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from test_project_registry import service
from test_project_registry_recovery import Crash


def marker(root, identity):
    directory = root / '.naiyue'
    directory.mkdir(exist_ok=True)
    (directory / 'workspace.json').write_text(json.dumps({'version': 1, 'workspace_id': identity}))


def test_import_refuses_conflicting_existing_marker(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    root = tmp_path / 'code'
    root.mkdir()
    path = tmp_path / 'legacy.sqlite'
    with ProjectStore(path) as store:
        store.sessions.bind_workspace(str(uuid4()), root)
    marker(root, str(uuid4()))
    before = path.read_bytes()
    with service(tmp_path) as projects:
        assert projects.import_legacy([path])[0]['error'] == 'project_identity_conflict'
        assert projects.list() == ()
    assert path.read_bytes() == before


@pytest.mark.parametrize('action', ['select', 'relocate'])
def test_confirmation_refuses_directory_of_other_legacy_project(tmp_path, action):
    from naiyue_test_agent.contracts.projects import ProjectError
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    with service(tmp_path) as projects:
        project = projects.add_local(first, trusted=True, operation_id='add')
        marker(first if action == 'select' else second, str(uuid4()))
        with pytest.raises(ProjectError, match='project_identity_conflict'):
            if action == 'select':
                projects.add_local(first, trusted=True, operation_id='select')
            else:
                projects.relocate(project.project_id, expected_root=first, root=second, trusted=True, operation_id='move')


def test_failed_import_retry_never_reports_pending_as_success(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    path = tmp_path / 'legacy.sqlite'
    with ProjectStore(path) as store:
        store.sessions.bind_workspace(str(uuid4()), tmp_path)
    with service(tmp_path) as projects:
        with projects.registry.database.transaction(write=True) as connection:
            connection.execute("CREATE TRIGGER fail_finish BEFORE UPDATE OF lifecycle ON projects WHEN NEW.lifecycle='active' BEGIN SELECT RAISE(ABORT,'injected'); END")
        first = projects.import_legacy([path])[0]
        assert first['error'] == 'project_operation_failed'
        second = projects.import_legacy([path])[0]
        assert second['project'] is None and second['error'] == 'project_operation_failed'
        assert projects.list() == ()
        with projects.registry.database.transaction(write=True) as connection:
            connection.execute('DROP TRIGGER fail_finish')
        assert projects.import_legacy([path])[0]['project'].lifecycle == 'active'


def test_final_registration_rejects_replaced_directory(monkeypatch, tmp_path):
    from naiyue_test_agent.application.projects import operations
    from naiyue_test_agent.contracts.projects import ProjectError
    root = tmp_path / 'code'
    root.mkdir()
    original = operations.initialize_reserved_store
    def replace_directory(project, **kwargs):
        root.rename(tmp_path / 'original-code')
        root.mkdir()
        return original(project, **kwargs)
    monkeypatch.setattr(operations, 'initialize_reserved_store', replace_directory)
    with service(tmp_path) as projects:
        with pytest.raises(ProjectError, match='project_location_changed'):
            projects.add_local(root, trusted=True, operation_id='add')
        assert projects.list() == ()


def test_unbound_store_with_unknown_business_data_is_not_adopted(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.project_store import ProjectStore
    original = ProjectRegistry.reserve
    def crash(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(ProjectRegistry, 'reserve', crash)
        with service(tmp_path) as projects:
            with pytest.raises(Crash):
                projects.add_local(tmp_path, trusted=True, operation_id='add')
    with sqlite3.connect(tmp_path / 'data/projects.sqlite') as connection:
        target = Path(connection.execute('SELECT database_path FROM projects').fetchone()[0])
    with ProjectStore(target) as store:
        with store.database.transaction(write=True) as connection:
            connection.execute('CREATE TABLE unknown_business(content TEXT)')
            connection.execute("INSERT INTO unknown_business VALUES('preserve')")
    before = target.read_bytes()
    with service(tmp_path) as projects:
        assert projects.list() == ()
        assert projects.operations()[0].error_code == 'project_database_unconfirmed'
    assert target.read_bytes() == before


def test_mkdir_race_never_creates_in_unselected_directory(monkeypatch, tmp_path):
    import os
    from naiyue_test_agent.contracts.projects import ProjectError
    parent, elsewhere = tmp_path / 'parent', tmp_path / 'elsewhere'
    parent.mkdir()
    elsewhere.mkdir()
    original = os.mkdir
    def racing_mkdir(path, *args, **kwargs):
        if str(path) in ('child', str(parent / 'child')):
            parent.rename(tmp_path / 'original-parent')
            parent.symlink_to(elsewhere, target_is_directory=True)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'mkdir', racing_mkdir)
    with service(tmp_path) as projects:
        with pytest.raises(ProjectError, match='project_location_changed'):
            projects.create_local(parent=parent, folder_name='child', trusted=True, operation_id='create')
        assert projects.list() == ()
    assert not (elsewhere / 'child').exists()
