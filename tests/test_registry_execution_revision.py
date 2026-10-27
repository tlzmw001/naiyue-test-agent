"""The additive registry migration and revision's transaction boundaries."""
from dataclasses import replace
import sqlite3
from uuid import uuid4

import pytest

from naiyue_test_agent.contracts.projects import LocalSource, ProjectError, RegisteredProject
from naiyue_test_agent.storage.project_registry import ProjectRegistry


def test_version1_registry_preserves_operations_and_adds_revision(tmp_path):
    path = tmp_path / 'projects.sqlite'
    project = RegisteredProject(str(uuid4()), 'name', LocalSource(tmp_path), tmp_path / 'sessions.sqlite', lifecycle='active')
    registry = ProjectRegistry(path)
    registry.reserve('seed', 'import_legacy', {'root': str(tmp_path)}, project, new=True, facts={'original': True})
    before = registry.operation('seed')
    registry.close()
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE projects DROP COLUMN revision')
        connection.execute('DROP TABLE execution_environments')
        connection.execute('DROP TABLE sandbox_operations')
        connection.execute('DROP TABLE git_inputs')
        connection.execute('DROP TABLE git_deliveries')
        connection.execute('PRAGMA user_version=1')
    registry = ProjectRegistry(path)
    try:
        assert registry.execution_facts(project.project_id).revision == 1
        assert registry.operation('seed') == before
        assert registry.database.read_rows('PRAGMA user_version') == [{'user_version': 6}]
    finally:
        registry.close()


def test_revision_tracks_trust_location_and_lifecycle_not_operation_progress(tmp_path):
    path = tmp_path / 'projects.sqlite'
    project = RegisteredProject(str(uuid4()), 'name', LocalSource(tmp_path), tmp_path / 'sessions.sqlite', lifecycle='active')
    registry = ProjectRegistry(path)
    try:
        registry.reserve('seed', 'import_legacy', {}, project, new=True)
        registry.finish('seed', project=project)
        assert registry.execution_facts(project.project_id).revision == 1
        registry.reserve('trust', 'select_existing', {}, project)
        project = replace(project, trusted=True)
        registry.finish('trust', project=project)
        assert registry.execution_facts(project.project_id).revision == 2
        registry.reserve('move', 'relocate', {}, project, lifecycle='migrating')
        assert registry.execution_facts(project.project_id).revision == 3
        registry.progress('move', 'directory_checked', {})
        registry.fail('move', 'project_operation_failed')
        assert registry.execution_facts(project.project_id).revision == 3
        project = replace(project, source=LocalSource(tmp_path / 'moved'))
        registry.finish('move', project=project)
        facts = registry.execution_facts(project.project_id)
        assert facts.revision == 4 and facts.lifecycle == 'active' and facts.root == tmp_path / 'moved'
        registry.reserve('remove', 'remove', {}, project, lifecycle='deleting')
        assert registry.execution_facts(project.project_id).revision == 5
        registry.finish('remove', remove=True)
        with pytest.raises(ProjectError, match='project_not_found'):
            registry.execution_facts(project.project_id)
    finally:
        registry.close()
