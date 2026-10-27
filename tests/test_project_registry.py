"""Project registration acts on real directories and separate SQLite stores."""
from pathlib import Path
from uuid import uuid4
import shutil

import pytest


def service(tmp_path, **kwargs):
    from naiyue_test_agent.application.projects.service import ProjectService
    from naiyue_test_agent.project_settings import ProjectsSettings
    return ProjectService(tmp_path / 'data', kwargs.pop('settings', ProjectsSettings()), **kwargs)


def test_add_existing_directory_without_marker_and_reopen(tmp_path):
    root = tmp_path / 'code'
    root.mkdir()
    (root / 'file').write_text('keep')
    with service(tmp_path) as projects:
        project = projects.add_local(root, trusted=True, operation_id='add')
        assert project.source.path == root.resolve()
        assert project.lifecycle == 'active' and project.trusted
        assert project.availability == 'available'
        assert project.database_path == tmp_path / 'data/workspaces' / project.project_id / 'sessions.sqlite'
        assert projects.add_local(root, trusted=True, operation_id='add') == project
        assert projects.add_local(root, trusted=True, operation_id='another').project_id == project.project_id
    assert sorted(p.name for p in root.iterdir()) == ['file']
    with service(tmp_path) as projects:
        assert projects.get(project.project_id) == project
        assert len(projects.list()) == 1
    from naiyue_test_agent.storage.project_store import ProjectStore
    with ProjectStore(project.database_path) as store:
        assert store.sessions.get_workspace_binding()['workspace_id'] == project.project_id


def test_paths_aliases_nested_projects_and_id_conflict(tmp_path):
    from naiyue_test_agent.contracts.projects import ProjectError
    root = tmp_path / 'code'
    root.mkdir()
    nested = root / 'child'
    nested.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    with service(tmp_path) as projects:
        parent = projects.add_local(root, trusted=True, operation_id='parent')
        assert projects.add_local(alias, trusted=True, operation_id='alias').project_id == parent.project_id
        child = projects.add_local(nested, trusted=True, operation_id='child')
        assert parent.project_id in child.nested_project_ids
        with pytest.raises(ProjectError, match='project_operation_conflict'):
            projects.add_local(nested, trusted=True, operation_id='parent')


def test_creation_default_is_optional_and_not_a_boundary(tmp_path):
    from naiyue_test_agent.project_settings import ProjectsSettings
    from naiyue_test_agent.contracts.projects import ProjectError
    default, other = tmp_path / 'default', tmp_path / 'other'
    default.mkdir()
    other.mkdir()
    settings = ProjectsSettings(default_parent_dir=default)
    with service(tmp_path, settings=settings) as projects:
        first = projects.create_local(folder_name='one', trusted=True, operation_id='one')
        second = projects.create_local(parent=other, folder_name='two', trusted=True, operation_id='two')
        assert first.source.path == default / 'one'
        assert second.source.path == other / 'two'
        with pytest.raises(ProjectError, match='project_directory_exists'):
            projects.create_local(parent=other, folder_name='two', trusted=True, operation_id='duplicate')
    assert not (other / 'two/.naiyue').exists()


@pytest.mark.parametrize('name', ['..', '.', '../escape', '/absolute', 'a/b', 'a\\b', ''])
def test_creation_rejects_invalid_name_without_creating_files(tmp_path, name):
    from naiyue_test_agent.contracts.projects import ProjectError
    with service(tmp_path) as projects:
        with pytest.raises(ProjectError, match='project_invalid_name'):
            projects.create_local(parent=tmp_path, folder_name=name, trusted=True, operation_id='bad')
        assert projects.list() == ()


def test_explicit_trust_and_missing_default(tmp_path):
    from naiyue_test_agent.contracts.projects import ProjectError
    with service(tmp_path) as projects:
        with pytest.raises(ProjectError, match='project_trust_required'):
            projects.add_local(tmp_path, trusted=False, operation_id='untrusted')
        with pytest.raises(ProjectError, match='project_parent_required'):
            projects.create_local(folder_name='x', trusted=True, operation_id='no-parent')
        assert projects.list() == ()


def test_import_preserves_uuid_database_and_missing_location(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    old = tmp_path / 'old'
    old.mkdir()
    identity = str(uuid4())
    database = tmp_path / 'data/workspaces' / identity / 'sessions.sqlite'
    with ProjectStore(database) as store:
        store.sessions.bind_workspace(identity, old)
        store.sessions.create('s', dict(thread_id='t', model_id='m', workspace_root=old,
            active_tool_names=[], permission_mode='normal'))
    before = database.read_bytes()
    old.rmdir()
    with service(tmp_path) as projects:
        result = projects.import_legacy()
        assert len(result) == 1 and result[0]['error'] is None
        project = projects.get(identity)
        assert project.database_path == database
        assert project.availability == 'missing' and not project.trusted
        assert projects.import_legacy()[0]['project'].project_id == identity
    assert database.read_bytes() == before
    with ProjectStore(database) as store:
        assert store.sessions.get('s')['workspace_root'] == str(old)


def test_import_copy_and_unbound_database_are_reported_without_guessing(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    root = tmp_path / 'code'
    root.mkdir()
    identity = str(uuid4())
    first, copy, unbound = [tmp_path / name for name in ('first.sqlite', 'copy.sqlite', 'unbound.sqlite')]
    with ProjectStore(first) as store:
        store.sessions.bind_workspace(identity, root)
    shutil.copyfile(first, copy)
    with ProjectStore(unbound):
        pass
    with service(tmp_path) as projects:
        results = projects.import_legacy([first, copy, unbound])
        assert results[0]['project'].project_id == identity
        assert results[1]['error'] == 'project_identity_conflict'
        assert results[2]['error'] == 'project_unbound_database'
        assert len(projects.list()) == 1


def test_relocate_keeps_old_session_roots_and_delete_retains_data(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    old, new = tmp_path / 'old', tmp_path / 'new'
    old.mkdir()
    new.mkdir()
    with service(tmp_path) as projects:
        project = projects.add_local(old, trusted=True, operation_id='add')
        with ProjectStore(project.database_path) as store:
            store.sessions.create('s', dict(thread_id='t', model_id='m', workspace_root=old,
                active_tool_names=[], permission_mode='normal'))
        moved = projects.relocate(project.project_id, expected_root=old, root=new,
            trusted=True, operation_id='move')
        assert moved.source.path == new
        with ProjectStore(project.database_path) as store:
            assert store.sessions.get('s')['workspace_root'] == str(old)
        result = projects.remove(project.project_id, operation_id='remove')
        assert result['data_retained'] is True
        assert projects.list() == ()
        assert projects.import_legacy() == ()
    assert old.is_dir() and new.is_dir() and project.database_path.is_file()


def test_git_source_records_no_fake_local_root_or_execution_database(tmp_path):
    with service(tmp_path) as projects:
        project = projects.add_git('https://example.invalid/repository.git', ref='main',
            name='remote', operation_id='git')
        assert project.source.url == 'https://example.invalid/repository.git'
        assert project.source.ref == 'main' and project.availability is None
        assert not project.database_path.exists()
