"""Registry records and durable cross-store operation progress."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3

from naiyue_test_agent.contracts.projects import (
    GitSource, LocalSource, ProjectError, ProjectOperation, RegisteredProject, ProjectExecutionFacts,
)
from .database import Database


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _project(row):
    source = LocalSource(Path(row['root'])) if row['source_kind'] == 'local' else GitSource(row['git_url'], row['git_ref'])
    return RegisteredProject(row['project_id'], row['name'], source, Path(row['database_path']),
                             row['lifecycle'], bool(row['trusted']), revision=row['revision'])


def _operation(row):
    if row is None:
        return None
    return ProjectOperation(row['operation_id'], row['action'], json.loads(row['request_json']),
        row['project_id'], row['stage'], json.loads(row['facts_json']), row['error_code'],
        json.loads(row['result_json']) if row['result_json'] is not None else None)


def _values(project):
    local = isinstance(project.source, LocalSource)
    root = str(project.source.path) if local else None
    return (project.project_id, project.name, 'local' if local else 'git', root,
        os.path.normcase(root) if local else None, None if local else project.source.url,
        None if local else project.source.ref, str(project.database_path), project.lifecycle, int(project.trusted))


class ProjectRegistry:
    def __init__(self, path):
        self.database = Database.open_registry(path)

    def close(self):
        self.database.close()

    def list(self, *, all_states=False):
        rows = self.database.read_rows('SELECT * FROM projects ORDER BY name,project_id')
        return tuple(_project(row) for row in rows if all_states or row['lifecycle'] == 'active')

    def get(self, project_id):
        rows = self.database.read_rows('SELECT * FROM projects WHERE project_id=?', (project_id,))
        if not rows:
            raise ProjectError('project_not_found')
        return _project(rows[0])

    def execution_facts(self, project_id):
        rows = self.database.read_rows('''SELECT project_id,lifecycle,trusted,source_kind,root,path_key,revision
            FROM projects WHERE project_id=?''', (project_id,))
        if not rows:
            raise ProjectError('project_not_found')
        row = rows[0]
        return ProjectExecutionFacts(row['project_id'], row['lifecycle'], bool(row['trusted']),
            row['source_kind'], Path(row['root']) if row['root'] is not None else None,
            row['path_key'], row['revision'])

    def by_path(self, path):
        rows = self.database.read_rows('SELECT * FROM projects WHERE path_key=?', (os.path.normcase(str(path)),))
        return _project(rows[0]) if rows else None

    def operation(self, operation_id):
        rows = self.database.read_rows('SELECT * FROM project_operations WHERE operation_id=?', (operation_id,))
        return _operation(rows[0]) if rows else None

    def operations(self):
        return tuple(_operation(row) for row in self.database.read_rows('SELECT * FROM project_operations ORDER BY rowid'))

    def was_removed(self, database_path):
        return any(op.action == 'remove' and op.stage == 'done'
                   and op.facts.get('database_path') == str(database_path) for op in self.operations())

    def reserve(self, operation_id, action, request, project, *, new=False, lifecycle=None, facts=None):
        with self.database.transaction(write=True) as connection:
            row = connection.execute('SELECT * FROM project_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if row is not None:
                operation = _operation(row)
                if operation.action != action or operation.request != request:
                    raise ProjectError('project_operation_conflict')
                return operation
            try:
                if connection.execute("SELECT 1 FROM project_operations WHERE project_id=? AND stage!='done' LIMIT 1",
                                      (project.project_id,)).fetchone():
                    raise ProjectError('project_operation_pending')
                if new:
                    connection.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?)', _values(project) + (1,))
                elif lifecycle is not None:
                    connection.execute('UPDATE projects SET lifecycle=?,revision=revision+1 WHERE project_id=? AND lifecycle!=?',
                                       (lifecycle, project.project_id, lifecycle))
                connection.execute('INSERT INTO project_operations VALUES(?,?,?,?,?,?,NULL,NULL)',
                    (operation_id, action, _json(request), project.project_id, 'reserved', _json(facts or {})))
            except sqlite3.IntegrityError as error:
                if error.sqlite_errorcode in (sqlite3.SQLITE_CONSTRAINT_UNIQUE, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY):
                    raise ProjectError('project_identity_conflict') from error
                raise
        return self.operation(operation_id)

    def progress(self, operation_id, stage, facts):
        with self.database.transaction(write=True) as connection:
            connection.execute('UPDATE project_operations SET stage=?,facts_json=?,error_code=NULL WHERE operation_id=?',
                               (stage, _json(facts), operation_id))

    def fail(self, operation_id, code):
        with self.database.transaction(write=True) as connection:
            connection.execute('UPDATE project_operations SET error_code=? WHERE operation_id=?', (code, operation_id))

    def finish(self, operation_id, *, project=None, result=None, remove=False):
        with self.database.transaction(write=True) as connection:
            operation = _operation(connection.execute('SELECT * FROM project_operations WHERE operation_id=?', (operation_id,)).fetchone())
            if project is not None:
                value = _values(replace(project, lifecycle='active'))
                before = connection.execute('SELECT * FROM projects WHERE project_id=?', (project.project_id,)).fetchone()
                changed = _values(_project(before)) != value
                try:
                    connection.execute('''UPDATE projects SET name=?,source_kind=?,root=?,path_key=?,git_url=?,git_ref=?,
                        database_path=?,lifecycle=?,trusted=?,revision=revision+? WHERE project_id=?''',
                        value[1:] + (int(changed),) + value[:1])
                except sqlite3.IntegrityError as error:
                    if error.sqlite_errorcode in (sqlite3.SQLITE_CONSTRAINT_UNIQUE, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY):
                        raise ProjectError('project_identity_conflict') from error
                    raise
            if remove:
                connection.execute('DELETE FROM projects WHERE project_id=?', (operation.project_id,))
            result = result or {'project_id': operation.project_id}
            connection.execute("UPDATE project_operations SET stage='done',result_json=?,error_code=NULL WHERE operation_id=?",
                               (_json(result), operation_id))
