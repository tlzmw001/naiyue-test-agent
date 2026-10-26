"""Registration cannot adopt changed locations or bypass resource ownership."""
from pathlib import Path
import sqlite3

import pytest

from test_project_registry import service
from test_project_registry_recovery import Crash


def test_parent_replaced_after_mkdir_planned_is_rejected(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    parent = tmp_path / 'parent'
    parent.mkdir()
    original = ProjectRegistry.progress
    def crash(self, identity, stage, facts):
        original(self, identity, stage, facts)
        if stage == 'mkdir_planned':
            raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(ProjectRegistry, 'progress', crash)
        with service(tmp_path) as projects:
            with pytest.raises(Crash):
                projects.create_local(parent=parent, folder_name='child', trusted=True, operation_id='create')
    parent.rename(tmp_path / 'original-parent')
    parent.mkdir()
    with service(tmp_path) as projects:
        assert projects.list() == ()
        assert projects.operations()[0].error_code == 'project_location_changed'
    assert not (parent / 'child').exists()


@pytest.mark.parametrize('state', ['running', 'interrupted', 'review_required'])
def test_delete_refuses_running_or_unconfirmed_project(tmp_path, state):
    from naiyue_test_agent.contracts.projects import ProjectError
    from naiyue_test_agent.storage.project_store import ProjectStore
    with service(tmp_path) as projects:
        project = projects.add_local(tmp_path, trusted=True, operation_id='add')
        with ProjectStore(project.database_path) as store:
            store.sessions.create('s', dict(thread_id='t', model_id='m', workspace_root=tmp_path,
                active_tool_names=[], permission_mode='normal'))
            with store.database.transaction(write=True) as connection:
                connection.execute('UPDATE naiyue_sessions SET state=?', (state,))
        with pytest.raises(ProjectError, match='project_in_use'):
            projects.remove(project.project_id, operation_id='delete')
        assert project.database_path.exists()


def test_pending_rename_cannot_be_overtaken_by_new_operation(monkeypatch, tmp_path):
    from naiyue_test_agent.contracts.projects import ProjectError
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    with service(tmp_path) as projects:
        project = projects.add_local(tmp_path, trusted=True, operation_id='add')
        with monkeypatch.context() as patch:
            patch.setattr(ProjectRegistry, 'finish', lambda *args, **kwargs: (_ for _ in ()).throw(Crash()))
            with pytest.raises(Crash):
                projects.rename(project.project_id, 'first', operation_id='first-rename')
        with pytest.raises(ProjectError, match='project_operation_pending'):
            projects.rename(project.project_id, 'second', operation_id='second-rename')
        assert projects.reconcile()[0]['error'] is None
        assert projects.get(project.project_id).name == 'first'


def test_reserved_store_wrong_binding_is_not_rebound(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.project_store import ProjectStore
    root, other = tmp_path / 'root', tmp_path / 'other'
    root.mkdir()
    other.mkdir()
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
        identity, path = connection.execute('SELECT project_id,database_path FROM projects').fetchone()
    with ProjectStore(path) as store:
        store.sessions.bind_workspace(identity, other)
    with service(tmp_path) as projects:
        assert projects.list() == ()
        assert projects.operations()[0].error_code == 'project_identity_conflict'


@pytest.mark.parametrize('boundary', ['finish', 'database_created'])
def test_git_initialization_is_not_repeated_after_recorded_success(monkeypatch, tmp_path, boundary):
    import subprocess
    from naiyue_test_agent.application.projects import operations
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    commands = []
    original_run = subprocess.run
    def run(argv, **kwargs):
        commands.append(tuple(argv))
        return original_run(argv, **kwargs)
    monkeypatch.setattr(operations.subprocess, 'run', run)
    original_progress = ProjectRegistry.progress
    def progress(self, identity, stage, facts):
        original_progress(self, identity, stage, facts)
        if boundary == stage:
            raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(ProjectRegistry, 'progress', progress)
        if boundary == 'finish':
            patch.setattr(ProjectRegistry, 'finish', lambda *args, **kwargs: (_ for _ in ()).throw(Crash()))
        with service(tmp_path) as projects:
            with pytest.raises(Crash):
                projects.create_local(parent=tmp_path, folder_name='git-code', trusted=True,
                    git_init=True, operation_id='create')
    with service(tmp_path) as projects:
        project = projects.create_local(parent=tmp_path, folder_name='git-code', trusted=True,
            git_init=True, operation_id='create')
        assert project.lifecycle == 'active' and (project.source.path / '.git').is_dir()
    assert len(commands) == 1


def test_delete_then_explicit_reimport_retains_original_identity(tmp_path):
    with service(tmp_path) as projects:
        project = projects.add_local(tmp_path, trusted=True, operation_id='add')
        first = projects.remove(project.project_id, operation_id='remove')
        assert projects.remove(project.project_id, operation_id='remove') == first
        assert projects.import_legacy() == ()
        result = projects.import_legacy([project.database_path])
        assert result[0]['project'].project_id == project.project_id
        assert result[0]['project'].trusted is False


def test_oversized_folder_component_is_rejected_before_reservation(tmp_path):
    from naiyue_test_agent.contracts.projects import ProjectError
    with service(tmp_path) as projects:
        with pytest.raises(ProjectError, match='project_invalid_name'):
            projects.create_local(parent=tmp_path, folder_name='a' * 32769, name='short',
                trusted=True, operation_id='oversized')
        assert projects.operations() == ()
