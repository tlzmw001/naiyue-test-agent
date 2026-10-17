"""Local SQLite metadata and one process owning the database instance."""

import logging
import json
import os
import sqlite3
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


logger = logging.getLogger(__name__)
STATES = {'ready', 'running', 'failed', 'interrupted', 'review_required'}


from .errors import SessionPersistenceError
from naiyue_test_agent.storage_files import (
    acquire_store_lock, check_store_file, open_store_file, release_store_lock,
)
from naiyue_test_agent.workspaces import check_workspace_relocation


class SessionStorage:
    def __init__(self, path):
        self.path = Path(path).expanduser().absolute()
        self._stack = ExitStack()
        self._lock = RLock()
        self._closed = False
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock_path = self.path.with_name(self.path.name + '.lock')
            fd = open_store_file(lock_path)
            self._stack.callback(os.close, fd)
            try:
                acquire_store_lock(fd)
            except BlockingIOError as error:
                raise SessionPersistenceError('storage_in_use') from error
            self._stack.callback(release_store_lock, fd)
            try:
                check_store_file(self.path)
                existing = True
            except FileNotFoundError:
                existing = False
                file_fd = open_store_file(self.path, exclusive_create=True)
                os.close(file_fd)
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = self.path.with_name(self.path.name + suffix)
                try:
                    check_store_file(sidecar)
                except FileNotFoundError:
                    pass
            self.connection = sqlite3.connect(self.path, check_same_thread=False)
            self._stack.callback(self.connection.close)
            self.connection.row_factory = sqlite3.Row
            if existing:
                version = self.connection.execute('SELECT version FROM naiyue_store_info').fetchall()
                if len(version) != 1 or version[0][0] not in (4, 5, 6):
                    raise SessionPersistenceError('unsupported_store_schema')
                if version[0][0] in (4, 5):
                    with self.connection:
                        self.connection.execute('BEGIN')
                        if version[0][0] == 4:
                            self.connection.execute("ALTER TABLE naiyue_sessions ADD COLUMN thinking_level TEXT NOT NULL DEFAULT 'model_default'")
                        self.connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN title TEXT')
                        self.connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0')
                        self.connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0')
                        rows = self.connection.execute('SELECT session_id FROM naiyue_sessions ORDER BY updated_at DESC,session_id').fetchall()
                        self.connection.executemany('UPDATE naiyue_sessions SET sort_order=? WHERE session_id=?',
                                                    [(index, row[0]) for index, row in enumerate(rows)])
                        self.connection.execute('UPDATE naiyue_store_info SET version=6')
            else:
                self.connection.executescript('''
                    CREATE TABLE naiyue_store_info(version INTEGER NOT NULL);
                    INSERT INTO naiyue_store_info VALUES(6);
                    CREATE TABLE naiyue_workspace(
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        workspace_id TEXT NOT NULL, root_path TEXT NOT NULL);
                    CREATE TABLE naiyue_sessions(
                        session_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE,
                        model_id TEXT NOT NULL, workspace_root TEXT,
                        state TEXT NOT NULL, run_id TEXT, checkpoint_id TEXT,
                        checkpoint_ns TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        last_run_status TEXT, active_tool_names_json TEXT NOT NULL,
                        permission_mode TEXT NOT NULL DEFAULT 'Default',
                        thinking_level TEXT NOT NULL DEFAULT 'model_default',
                        title TEXT, pinned INTEGER NOT NULL DEFAULT 0,
                        sort_order INTEGER NOT NULL DEFAULT 0);
                ''')
            self.checkpoint_connection = sqlite3.connect(self.path, check_same_thread=False)
            self._stack.callback(self.checkpoint_connection.close)
        except (OSError, sqlite3.Error, SessionPersistenceError) as error:
            logger.error('session_storage_open_failed error_type=%s', type(error).__name__)
            try:
                self.close()
            except SessionPersistenceError:
                pass  # close() logged the secondary failure; preserve the opening failure.
            if isinstance(error, SessionPersistenceError):
                raise
            raise SessionPersistenceError('storage_unavailable') from error

    def get_workspace_binding(self):
        rows = self._read('SELECT workspace_id,root_path FROM naiyue_workspace')
        if len(rows) > 1:
            raise SessionPersistenceError('invalid_workspace_binding')
        return rows[0] if rows else None

    def bind_workspace(self, workspace_id, root_path):
        root = Path(root_path).expanduser().resolve(strict=True)
        try:
            with self._lock, self.connection:
                self.connection.execute('BEGIN')
                binding = self.get_workspace_binding()
                if binding is None:
                    if self.connection.execute('SELECT 1 FROM naiyue_sessions LIMIT 1').fetchone():
                        raise SessionPersistenceError('workspace_database_mismatch')
                    self.connection.execute('INSERT INTO naiyue_workspace VALUES(1,?,?)', (workspace_id, str(root)))
                    return
                if binding['workspace_id'] != workspace_id:
                    raise SessionPersistenceError('workspace_database_mismatch')
                check_workspace_relocation(workspace_id, binding['root_path'], root)
                if self.connection.execute('SELECT 1 FROM naiyue_sessions WHERE workspace_root IS NULL OR workspace_root != ? LIMIT 1',
                                           (binding['root_path'],)).fetchone():
                    raise SessionPersistenceError('workspace_database_mismatch')
                self.connection.execute('UPDATE naiyue_workspace SET root_path=? WHERE singleton=1', (str(root),))
                self.connection.execute('UPDATE naiyue_sessions SET workspace_root=?', (str(root),))
        except (sqlite3.Error, OSError, ValueError, SessionPersistenceError) as error:
            logger.error('workspace_binding_failed error_type=%s', type(error).__name__)
            if isinstance(error, SessionPersistenceError):
                raise
            raise SessionPersistenceError('workspace_binding_failed') from error

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec='microseconds')

    def _write(self, sql, values):
        try:
            with self._lock, self.connection:
                cursor = self.connection.execute(sql, values)
                if cursor.rowcount != 1:
                    raise SessionPersistenceError('session_not_found')
        except sqlite3.Error as error:
            logger.error('session_storage_write_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_save_unconfirmed') from error

    def _read(self, sql, values=()):
        try:
            with self._lock:
                return [dict(row) for row in self.connection.execute(sql, values).fetchall()]
        except sqlite3.Error as error:
            logger.error('session_storage_read_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_read_failed') from error

    def create(self, session_id, session):
        binding = self.get_workspace_binding()
        if binding and (session['workspace_root'] is None or
                        Path(session['workspace_root']).resolve(strict=True) != Path(binding['root_path'])):
            raise SessionPersistenceError('workspace_database_mismatch')
        now = self._now()
        self._write('''INSERT INTO naiyue_sessions
            (session_id,thread_id,model_id,workspace_root,state,created_at,updated_at,active_tool_names_json,permission_mode,sort_order)
            VALUES(?,?,?,?,?,?,?,?,?,(SELECT COALESCE(MIN(sort_order),0)-1 FROM naiyue_sessions WHERE workspace_root IS ?))''', (session_id, session['thread_id'], session['model_id'],
            str(session['workspace_root']) if session['workspace_root'] is not None else None,
            'ready', now, now, json.dumps(session['active_tool_names']), session['permission_mode'],
            str(session['workspace_root']) if session['workspace_root'] is not None else None))

    def get(self, session_id):
        rows = self._read('SELECT * FROM naiyue_sessions WHERE session_id=?', (session_id,))
        if not rows:
            raise SessionPersistenceError('session_not_found')
        row = rows[0]
        if row['state'] not in STATES or not row['thread_id'] or not row['model_id']:
            raise SessionPersistenceError('invalid_session_metadata')
        return row

    def list_sessions(self):
        return self._read('SELECT * FROM naiyue_sessions ORDER BY pinned DESC,sort_order ASC,created_at DESC,session_id ASC')

    def update_presentation(self, session_id, *, title=None, pinned=None):
        if title is None and pinned is None:
            raise ValueError('invalid_session_presentation')
        if title is not None:
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120 or any(ord(c) < 32 or ord(c) == 127 for c in title):
                raise ValueError('invalid_session_presentation')
            title = title.strip()
        if pinned is not None and type(pinned) is not bool:
            raise ValueError('invalid_session_presentation')
        self._write('UPDATE naiyue_sessions SET title=COALESCE(?,title),pinned=COALESCE(?,pinned) WHERE session_id=?',
                    (title, pinned, session_id))

    def reorder(self, workspace_root, identities):
        root = str(workspace_root) if workspace_root is not None else None
        try:
            with self._lock, self.connection:
                self.connection.execute('BEGIN')
                rows = self.connection.execute('SELECT session_id,pinned FROM naiyue_sessions WHERE workspace_root IS ?', (root,)).fetchall()
                pins = {row['session_id']: row['pinned'] for row in rows}
                if len(identities) != len(pins) or set(identities) != set(pins):
                    raise ValueError('stale_session_order')
                if [pins[key] for key in identities] != sorted(pins.values(), reverse=True):
                    raise ValueError('invalid_session_order')
                self.connection.executemany('UPDATE naiyue_sessions SET sort_order=? WHERE session_id=?', enumerate(identities))
        except sqlite3.Error as error:
            logger.error('session_order_save_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_save_unconfirmed') from error

    def delete(self, session_id, child_threads):
        try:
            with self._lock, self.connection:
                self.connection.execute('BEGIN')
                row = self.get(session_id)
                threads = [(key,) for key in {row['thread_id'], *child_threads}]
                # One transaction across the current official SQLite tables and our metadata.
                self.connection.executemany('DELETE FROM writes WHERE thread_id=?', threads)
                self.connection.executemany('DELETE FROM checkpoints WHERE thread_id=?', threads)
                self.connection.execute('DELETE FROM naiyue_sessions WHERE session_id=?', (session_id,))
        except sqlite3.Error as error:
            logger.error('session_delete_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_delete_unconfirmed') from error

    def select_model(self, session_id, model_id):
        self._write("UPDATE naiyue_sessions SET model_id=?,thinking_level='model_default',updated_at=? WHERE session_id=?",
                    (model_id, self._now(), session_id))

    def select_thinking(self, session_id, level):
        self._write('UPDATE naiyue_sessions SET thinking_level=?,updated_at=? WHERE session_id=?',
                    (level, self._now(), session_id))

    def select_active_tools(self, session_id, names):
        self._write('UPDATE naiyue_sessions SET active_tool_names_json=?,updated_at=? WHERE session_id=?',
                    (json.dumps(list(names)), self._now(), session_id))

    def select_permission_mode(self, session_id, mode):
        self._write('UPDATE naiyue_sessions SET permission_mode=?,updated_at=? WHERE session_id=?',
                    (mode, self._now(), session_id))

    def mark_running(self, session_id, run_id):
        self._write("UPDATE naiyue_sessions SET state='running',run_id=?,updated_at=? WHERE session_id=?",
                    (run_id, self._now(), session_id))

    def finish_run(self, session_id, state, checkpoint_id, checkpoint_ns, last_run_status):
        if state not in STATES:
            raise SessionPersistenceError('invalid_session_state')
        self._write('''UPDATE naiyue_sessions SET state=?,checkpoint_id=?,checkpoint_ns=?,
            last_run_status=?,updated_at=? WHERE session_id=?''',
            (state, checkpoint_id, checkpoint_ns, last_run_status, self._now(), session_id))

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._stack.close()
            except (OSError, sqlite3.Error) as error:
                logger.error('session_storage_close_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('storage_close_failed') from error

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            self.close()
        except SessionPersistenceError:
            if error is None:
                raise
