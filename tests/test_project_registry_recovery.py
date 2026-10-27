"""Crash at durable boundaries, close, and reconcile using a new owner."""
from pathlib import Path
from uuid import uuid4
import sqlite3

import pytest

from test_project_registry import service


class Crash(BaseException):
    pass


@pytest.mark.parametrize('boundary', ['reserve', 'database_created', 'database_ready', 'finish_before', 'finish_after'])
def test_interrupted_registration_reopens_without_duplicate_identity(monkeypatch, tmp_path, boundary):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    root = tmp_path / 'code'
    root.mkdir()
    original_reserve, original_progress, original_finish = ProjectRegistry.reserve, ProjectRegistry.progress, ProjectRegistry.finish
    def reserve(self, *args, **kwargs):
        result = original_reserve(self, *args, **kwargs)
        if boundary == 'reserve':
            raise Crash()
        return result
    def progress(self, identity, stage, facts):
        original_progress(self, identity, stage, facts)
        if boundary == stage:
            raise Crash()
    def finish(self, *args, **kwargs):
        if boundary == 'finish_before':
            raise Crash()
        result = original_finish(self, *args, **kwargs)
        if boundary == 'finish_after':
            raise Crash()
        return result
    with monkeypatch.context() as patch:
        patch.setattr(ProjectRegistry, 'reserve', reserve)
        patch.setattr(ProjectRegistry, 'progress', progress)
        patch.setattr(ProjectRegistry, 'finish', finish)
        with service(tmp_path) as projects:
            with pytest.raises(Crash):
                projects.add_local(root, trusted=True, operation_id='request')
    with service(tmp_path) as projects:
        project = projects.add_local(root, trusted=True, operation_id='request')
        assert project.lifecycle == 'active'
        assert len(projects.list()) == 1
        assert projects.operations()[0].stage == 'done'
    assert len(list((tmp_path / 'data/workspaces').glob('*/sessions.sqlite'))) == 1
    assert not (root / '.naiyue').exists()


def test_directory_creation_unknown_window_does_not_adopt_existing_directory(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    original = ProjectRegistry.progress
    def fail_directory_receipt(self, identity, stage, facts):
        if stage == 'directory_created':
            raise Crash()
        return original(self, identity, stage, facts)
    with monkeypatch.context() as patch:
        patch.setattr(ProjectRegistry, 'progress', fail_directory_receipt)
        with service(tmp_path) as projects:
            with pytest.raises(Crash):
                projects.create_local(parent=tmp_path, folder_name='created', trusted=True, operation_id='create')
    assert (tmp_path / 'created').is_dir()
    with service(tmp_path) as projects:
        assert projects.list() == ()
        operation = projects.operations()[0]
        assert operation.stage == 'mkdir_planned'
        assert operation.error_code == 'project_creation_unconfirmed'
        assert projects.reconcile()[0]['error'] == 'project_creation_unconfirmed'
    assert (tmp_path / 'created').is_dir()
    assert not list((tmp_path / 'data/workspaces').glob('*/sessions.sqlite'))


def test_existing_selection_retries_with_original_request_id(tmp_path):
    with service(tmp_path) as projects:
        first = projects.add_local(tmp_path, trusted=True, operation_id='first')
        second = projects.add_local(tmp_path, trusted=True, operation_id='second')
        assert projects.add_local(tmp_path, trusted=True, operation_id='second') == second == first


def test_pending_import_revalidates_database_identity_on_recovery(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.project_store import ProjectStore
    path = tmp_path / 'legacy.sqlite'
    root = tmp_path / 'old'
    root.mkdir()
    identity = str(uuid4())
    with ProjectStore(path) as store:
        store.sessions.bind_workspace(identity, root)
    original = ProjectRegistry.reserve
    def crash(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(ProjectRegistry, 'reserve', crash)
        with service(tmp_path) as projects:
            with pytest.raises(Crash):
                projects.import_legacy([path])
    with sqlite3.connect(path) as connection:
        connection.execute('UPDATE naiyue_workspace SET workspace_id=?', (str(uuid4()),))
    with service(tmp_path) as projects:
        assert projects.list() == ()
        assert projects.operations()[0].error_code == 'project_identity_conflict'


def test_failed_finish_rolls_back_registry_and_releases_lock_for_retry(monkeypatch, tmp_path):
    from naiyue_test_agent.contracts.projects import ProjectError
    root = tmp_path / 'code'
    root.mkdir()
    with service(tmp_path) as projects:
        registry = projects.registry
        with registry.database.transaction(write=True) as connection:
            connection.execute("CREATE TRIGGER fail_finish BEFORE UPDATE OF lifecycle ON projects WHEN NEW.lifecycle='active' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with pytest.raises(ProjectError, match='project_operation_failed'):
            projects.add_local(root, trusted=True, operation_id='add')
        assert projects.list() == ()
        assert projects.operations()[0].stage == 'database_ready'
        with registry.database.transaction(write=True) as connection:
            connection.execute('DROP TRIGGER fail_finish')
        project = projects.add_local(root, trusted=True, operation_id='add')
        assert project.lifecycle == 'active'


def test_imported_position_needs_explicit_trust_and_reimport_respects_relocation(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    old, new = tmp_path / 'old', tmp_path / 'new'
    old.mkdir()
    new.mkdir()
    path = tmp_path / 'legacy.sqlite'
    identity = str(uuid4())
    with ProjectStore(path) as store:
        store.sessions.bind_workspace(identity, old)
    with service(tmp_path) as projects:
        imported = projects.import_legacy([path])[0]['project']
        assert not imported.trusted
        assert projects.add_local(old, trusted=True, operation_id='trust').trusted
        projects.relocate(identity, expected_root=old, root=new, trusted=True, operation_id='move')
        assert projects.import_legacy([path])[0]['project'].source.path == new


def test_reimport_uses_confirmed_registry_location_after_old_location_is_reused(tmp_path):
    import json
    from naiyue_test_agent.storage.project_store import ProjectStore
    old, new = tmp_path / 'old', tmp_path / 'new'
    old.mkdir()
    new.mkdir()
    path = tmp_path / 'legacy.sqlite'
    identity = str(uuid4())
    with ProjectStore(path) as store:
        store.sessions.bind_workspace(identity, old)
    with service(tmp_path) as projects:
        projects.import_legacy([path])
        projects.relocate(identity, expected_root=old, root=new, trusted=True, operation_id='move')
        (old / '.naiyue').mkdir()
        (old / '.naiyue/workspace.json').write_text(json.dumps({'version': 1, 'workspace_id': str(uuid4())}))
        result = projects.import_legacy([path])[0]
        assert result['error'] is None and result['project'].source.path == new
