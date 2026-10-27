"""Session metadata and stable project binding."""

import json
import logging
import sqlite3
from pathlib import Path

from naiyue_test_agent.storage.errors import SessionPersistenceError
from .queries import read_rows, write_one, utc_now, workspace_binding

from .queries import STATES
from naiyue_test_agent.workspaces import check_workspace_relocation


logger = logging.getLogger(__name__)


class SessionRepo:
    def __init__(self, database):
        self.database = database
        self.execution_location = None

    def get_workspace_binding(self):
        with self.database.transaction(write=False, error_code='session_read_failed') as connection:
            return workspace_binding(connection)

    def bind_git_project(self,project_id):
        with self.database.transaction(write=True,error_code='workspace_binding_failed') as connection:
            binding=workspace_binding(connection)
            if binding is not None and binding!={'workspace_id':project_id,'root_path':None}:
                raise SessionPersistenceError('workspace_database_mismatch')
            if binding is None:
                connection.execute("INSERT INTO naiyue_project_identity VALUES(?,'git')",(project_id,))

    def bind_project_execution(self, project_id):
        with self.database.transaction(write=True, error_code='workspace_binding_failed') as connection:
            binding = workspace_binding(connection)
            if binding is None or binding['workspace_id'] != project_id:
                raise SessionPersistenceError('workspace_database_mismatch')
            connection.execute("UPDATE naiyue_sessions SET execution_binding='project_local' WHERE project_id=? AND execution_root_kind='local' AND execution_binding='local'", (project_id,))
            connection.execute("UPDATE naiyue_subagent_instances SET execution_binding='project_local' WHERE project_id=? AND execution_root_kind='local' AND execution_binding='local'", (project_id,))

    def bind_workspace(self, workspace_id, root_path):
        with self.database.transaction(write=True, error_code='workspace_binding_failed') as connection:
            root = Path(root_path).expanduser().resolve(strict=True)
            try:
                binding = workspace_binding(connection)
                if binding is None:
                    if connection.execute('SELECT 1 FROM naiyue_sessions LIMIT 1').fetchone():
                        raise SessionPersistenceError('workspace_database_mismatch')
                    connection.execute('INSERT INTO naiyue_workspace VALUES(1,?,?)', (workspace_id, str(root)))
                    connection.execute("INSERT INTO naiyue_project_identity VALUES(?,'local')",(workspace_id,))
                    return
                if binding['workspace_id'] != workspace_id:
                    raise SessionPersistenceError('workspace_database_mismatch')
                check_workspace_relocation(workspace_id, binding['root_path'], root)
                connection.execute('UPDATE naiyue_workspace SET root_path=? WHERE singleton=1', (str(root),))
                scope = (str(root), workspace_id, binding['root_path'])
                connection.execute("UPDATE naiyue_sessions SET workspace_root=? WHERE project_id=? AND execution_root_kind='local' AND workspace_root=?", scope)
                connection.execute("UPDATE naiyue_subagent_instances SET execution_root=? WHERE project_id=? AND execution_root_kind='local' AND execution_root=?", scope)
            except (sqlite3.Error, OSError, ValueError, SessionPersistenceError) as error:
                logger.error('workspace_binding_failed error_type=%s', type(error).__name__)
                if isinstance(error, SessionPersistenceError):
                    raise
                raise SessionPersistenceError('workspace_binding_failed') from error

    def create(self, session_id, session):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            binding = workspace_binding(connection)
            expected_root = self.execution_location or (Path(binding['root_path']) if binding and binding['root_path'] else None)
            project_id = binding['workspace_id'] if binding else None
            if session.get('project_id', project_id) != project_id:
                raise SessionPersistenceError('workspace_database_mismatch')
            kind = session.get('execution_root_kind', 'local' if session['workspace_root'] is not None else None)
            execution_binding = session.get('execution_binding', kind)
            if execution_binding == 'project_local':
                if binding is None or session.get('project_id') != project_id or kind != 'local':
                    raise SessionPersistenceError('invalid_execution_identity')
            elif execution_binding == 'sandbox':
                candidate=dict(project_id=project_id,execution_binding='sandbox',execution_root_kind=kind,
                    workspace_root=session['workspace_root'],execution_setup_json=json.dumps(session.get('execution_setup')))
                from .execution_storage import root_from_row
                root_from_row(candidate)
            elif execution_binding not in (None, 'local'):
                raise SessionPersistenceError('invalid_execution_identity')
            elif kind != ('local' if session['workspace_root'] is not None else None):
                raise SessionPersistenceError('invalid_execution_identity')
            elif binding and (session['workspace_root'] is None or
                              Path(session['workspace_root']).resolve(strict=True) != expected_root):
                raise SessionPersistenceError('workspace_database_mismatch')
            now = utc_now()
            if binding:
                order = connection.execute('SELECT COALESCE(MIN(sort_order),0)-1 FROM naiyue_sessions WHERE project_id=?',
                    (project_id,)).fetchone()[0]
            else:
                order = connection.execute('SELECT COALESCE(MIN(sort_order),0)-1 FROM naiyue_sessions WHERE workspace_root IS ?',
                    (str(session['workspace_root']) if session['workspace_root'] is not None else None,)).fetchone()[0]
            write_one(connection, '''INSERT INTO naiyue_sessions
                (session_id,thread_id,model_id,workspace_root,state,created_at,updated_at,active_tool_names_json,permission_mode,project_id,execution_root_kind,sort_order)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (session_id, session['thread_id'], session['model_id'],
                str(session['workspace_root']) if session['workspace_root'] is not None else None,
                'ready', now, now, json.dumps(session['active_tool_names']), session['permission_mode'], project_id, kind, order))
            connection.execute('UPDATE naiyue_sessions SET execution_binding=?,last_execution_path=?,execution_setup_json=? WHERE session_id=?',
                (execution_binding, session.get('last_execution_path', str(session['workspace_root']) if session['workspace_root'] is not None else None),
                 json.dumps(session['execution_setup'],sort_keys=True) if session.get('execution_setup') is not None else None,session_id))

    def get(self, session_id):
        with self.database.transaction(write=False, error_code='session_read_failed') as connection:
            rows = read_rows(connection, 'SELECT * FROM naiyue_sessions WHERE session_id=?', (session_id,))
            if not rows:
                raise SessionPersistenceError('session_not_found')
            row = rows[0]
            if row['state'] not in STATES or not row['thread_id'] or not row['model_id']:
                raise SessionPersistenceError('invalid_session_metadata')
            return row

    def list_sessions(self):
        with self.database.transaction(write=False, error_code='session_read_failed') as connection:
            return read_rows(connection, 'SELECT * FROM naiyue_sessions ORDER BY pinned DESC,sort_order ASC,created_at DESC,session_id ASC')

    def list_workspace_sessions(self, workspace_root):
        with self.database.transaction(write=False, error_code='session_read_failed') as connection:
            root = str(workspace_root) if workspace_root is not None else None
            binding = workspace_binding(connection)
            expected_root = str(self.execution_location or binding['root_path']) if binding and (self.execution_location or binding['root_path']) is not None else None
            if binding and root != expected_root:
                return []
            if binding:
                return read_rows(connection, '''SELECT session_id,model_id,workspace_root,project_id,execution_root_kind,execution_binding,
                    state,updated_at,last_run_status,title,pinned FROM naiyue_sessions WHERE project_id=?
                    ORDER BY pinned DESC,sort_order ASC,created_at DESC,session_id ASC''', (binding['workspace_id'],))
            return read_rows(connection, '''SELECT session_id,model_id,workspace_root,execution_binding,state,updated_at,last_run_status,title,pinned
                FROM naiyue_sessions WHERE workspace_root IS ?
                ORDER BY pinned DESC,sort_order ASC,created_at DESC,session_id ASC''', (root,))

    def update_presentation(self, session_id, *, title=None, pinned=None):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            if title is None and pinned is None:
                raise ValueError('invalid_session_presentation')
            if title is not None:
                if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120 or any(ord(c) < 32 or ord(c) == 127 for c in title):
                    raise ValueError('invalid_session_presentation')
                title = title.strip()
            if pinned is not None and type(pinned) is not bool:
                raise ValueError('invalid_session_presentation')
            write_one(connection, 'UPDATE naiyue_sessions SET title=COALESCE(?,title),pinned=COALESCE(?,pinned) WHERE session_id=?',
                        (title, pinned, session_id))

    def ensure_title(self, session_id, title):
        with self.database.transaction(write=True, error_code='session_title_unavailable') as connection:
            try:
                connection.execute('UPDATE naiyue_sessions SET title=? WHERE session_id=? AND title IS NULL',
                                        (title, session_id))
            except sqlite3.Error as error:
                logger.error('session_title_write_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('session_title_unavailable') from error

    def reorder(self, workspace_root, identities):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            root = str(workspace_root) if workspace_root is not None else None
            try:
                binding = workspace_binding(connection)
                expected_root = str(self.execution_location or binding['root_path']) if binding and (self.execution_location or binding['root_path']) is not None else None
                if binding and root != expected_root:
                    raise ValueError('stale_session_order')
                rows = (connection.execute('SELECT session_id,pinned FROM naiyue_sessions WHERE project_id=?', (binding['workspace_id'],)).fetchall()
                    if binding else connection.execute('SELECT session_id,pinned FROM naiyue_sessions WHERE workspace_root IS ?', (root,)).fetchall())
                pins = {row['session_id']: row['pinned'] for row in rows}
                if len(identities) != len(pins) or set(identities) != set(pins):
                    raise ValueError('stale_session_order')
                if [pins[key] for key in identities] != sorted(pins.values(), reverse=True):
                    raise ValueError('invalid_session_order')
                connection.executemany('UPDATE naiyue_sessions SET sort_order=? WHERE session_id=?', enumerate(identities))
            except sqlite3.Error as error:
                logger.error('session_order_save_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('session_save_unconfirmed') from error

    def select_model(self, session_id, model_id):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            write_one(connection, "UPDATE naiyue_sessions SET model_id=?,thinking_level='model_default',updated_at=? WHERE session_id=?",
                        (model_id, utc_now(), session_id))

    def select_thinking(self, session_id, level):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            write_one(connection, 'UPDATE naiyue_sessions SET thinking_level=?,updated_at=? WHERE session_id=?',
                        (level, utc_now(), session_id))

    def select_active_tools(self, session_id, names):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            write_one(connection, 'UPDATE naiyue_sessions SET active_tool_names_json=?,updated_at=? WHERE session_id=?',
                        (json.dumps(list(names)), utc_now(), session_id))

    def select_permission_mode(self, session_id, mode):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            write_one(connection, 'UPDATE naiyue_sessions SET permission_mode=?,updated_at=? WHERE session_id=?',
                        (mode, utc_now(), session_id))

    def mark_running(self, session_id, run_id):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            write_one(connection, "UPDATE naiyue_sessions SET state='running',run_id=?,updated_at=? WHERE session_id=?",
                        (run_id, utc_now(), session_id))
