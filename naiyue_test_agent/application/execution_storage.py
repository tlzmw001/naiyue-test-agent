"""Schema18 execution identity mapping, without touching saved filesystem locations."""

from pathlib import Path

from naiyue_test_agent.execution_roots import ExecutionRoot
from .errors import SessionPersistenceError


def initialize(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 18:
        return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        binding = connection.execute('SELECT workspace_id,root_path FROM naiyue_workspace').fetchone()
        if binding and connection.execute('''SELECT 1 FROM naiyue_sessions
                WHERE workspace_root IS NULL OR workspace_root!=? LIMIT 1''', (binding['root_path'],)).fetchone():
            raise SessionPersistenceError('workspace_database_mismatch')
        connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN project_id TEXT')
        connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN execution_root_kind TEXT')
        connection.execute("UPDATE naiyue_sessions SET execution_root_kind='local' WHERE workspace_root IS NOT NULL")
        if binding:
            connection.execute('UPDATE naiyue_sessions SET project_id=?', (binding['workspace_id'],))
        connection.execute('ALTER TABLE naiyue_subagent_instances ADD COLUMN project_id TEXT')
        connection.execute('ALTER TABLE naiyue_subagent_instances ADD COLUMN execution_root TEXT')
        connection.execute('ALTER TABLE naiyue_subagent_instances ADD COLUMN execution_root_kind TEXT')
        connection.execute('''UPDATE naiyue_subagent_instances SET
            project_id=(SELECT p.project_id FROM naiyue_sessions p WHERE p.session_id=naiyue_subagent_instances.session_id),
            execution_root=(SELECT p.workspace_root FROM naiyue_sessions p WHERE p.session_id=naiyue_subagent_instances.session_id),
            execution_root_kind=(SELECT p.execution_root_kind FROM naiyue_sessions p WHERE p.session_id=naiyue_subagent_instances.session_id)''')
        connection.execute('UPDATE naiyue_store_info SET version=18')


def root_from_row(row, *, child=False):
    path = row['execution_root'] if child else row['workspace_root']
    kind = row['execution_root_kind']
    if path is None:
        if kind is not None:
            raise SessionPersistenceError('invalid_execution_identity')
        return None
    if kind not in ('local', 'worktree'):
        raise SessionPersistenceError('invalid_execution_identity')
    try:
        return ExecutionRoot(Path(path), kind)
    except (TypeError, ValueError) as error:
        raise SessionPersistenceError('invalid_execution_identity') from error
