"""Schema18 execution identity mapping, without touching saved filesystem locations."""

from pathlib import Path

from naiyue_test_agent.execution_roots import ExecutionRoot
from naiyue_test_agent.storage.errors import SessionPersistenceError


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
    binding = row.get('execution_binding')
    kind = row['execution_root_kind']
    if binding == 'sandbox':
        try:
            from naiyue_test_agent.contracts.session_environment import saved_setup
            from naiyue_test_agent.contracts.git_inputs import input_id
            if row['project_id'] is None or kind is not None:
                raise ValueError('invalid_binding')
            if child:
                input_id(row['environment_binding_id'])
            else:
                saved_setup(row)
            if row['execution_root' if child else 'workspace_root'] is not None:
                raise ValueError('invalid_binding')
        except (ValueError,TypeError,KeyError) as error:
            raise SessionPersistenceError('invalid_execution_identity') from error
        return None
    if binding not in (None, 'local', 'project_local'):
        raise SessionPersistenceError('invalid_execution_identity')
    if 'execution_binding' in row and binding is None and kind is not None:
        raise SessionPersistenceError('invalid_execution_identity')
    if binding == 'project_local':
        if row['project_id'] is None or kind != 'local':
            raise SessionPersistenceError('invalid_execution_identity')
        # A project binding contains no saved execution path. The trusted host
        # resolves it from the registry; audit fields are deliberately ignored.
        return None
    path = row['execution_root'] if child else row['workspace_root']
    if path is None:
        if kind is not None:
            raise SessionPersistenceError('invalid_execution_identity')
        return None
    if kind not in ('local', 'worktree'):
        raise SessionPersistenceError('invalid_execution_identity')
    try:
        return ExecutionRoot(Path(path), kind, binding=binding or 'local')
    except (TypeError, ValueError) as error:
        raise SessionPersistenceError('invalid_execution_identity') from error


def initialize_project_local(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 19:
        return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN execution_binding TEXT')
        connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN last_execution_path TEXT')
        connection.execute('ALTER TABLE naiyue_subagent_instances ADD COLUMN execution_binding TEXT')
        connection.execute('ALTER TABLE naiyue_subagent_instances ADD COLUMN last_execution_path TEXT')
        connection.execute("UPDATE naiyue_sessions SET execution_binding=CASE WHEN execution_root_kind IS NOT NULL THEN 'local' END,last_execution_path=workspace_root")
        connection.execute("UPDATE naiyue_subagent_instances SET execution_binding=CASE WHEN execution_root_kind IS NOT NULL THEN 'local' END,last_execution_path=execution_root")
        connection.execute('UPDATE naiyue_store_info SET version=19')


def initialize_environments(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 20:
        return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE naiyue_project_identity(project_id TEXT PRIMARY KEY,source_kind TEXT NOT NULL)')
        connection.execute("INSERT INTO naiyue_project_identity SELECT workspace_id,'local' FROM naiyue_workspace")
        connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN execution_setup_json TEXT')
        connection.execute('ALTER TABLE naiyue_subagent_instances ADD COLUMN environment_binding_id TEXT')
        connection.execute('UPDATE naiyue_store_info SET version=20')
