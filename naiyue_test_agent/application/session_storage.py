"""Private local SQLite metadata and one process owning the database instance."""

import fcntl
import logging
import os
import sqlite3
import stat
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


logger = logging.getLogger(__name__)
STATES = {'ready', 'running', 'failed', 'interrupted', 'review_required'}


from .errors import SessionPersistenceError


class SessionStorage:
    def __init__(self, path):
        self.path = Path(path).expanduser().absolute()
        self._stack = ExitStack()
        self._lock = RLock()
        self._closed = False
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock_path = self.path.with_name(self.path.name + '.lock')
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self._stack.callback(os.close, fd)
            self._check_private(lock_path)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            existing = self.path.exists() or self.path.is_symlink()
            if not existing:
                file_fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(file_fd)
            self._check_private(self.path)
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = self.path.with_name(self.path.name + suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    self._check_private(sidecar)
            self.connection = sqlite3.connect(self.path, check_same_thread=False)
            self._stack.callback(self.connection.close)
            self.connection.row_factory = sqlite3.Row
            if existing:
                version = self.connection.execute('SELECT version FROM naiyue_store_info').fetchall()
                if len(version) != 1 or version[0][0] != 1:
                    raise SessionPersistenceError('unsupported_store_schema')
            else:
                self.connection.executescript('''
                    CREATE TABLE naiyue_store_info(version INTEGER NOT NULL);
                    INSERT INTO naiyue_store_info VALUES(1);
                    CREATE TABLE naiyue_sessions(
                        session_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE,
                        model_id TEXT NOT NULL, workspace_root TEXT,
                        state TEXT NOT NULL, run_id TEXT, checkpoint_id TEXT,
                        checkpoint_ns TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        last_run_status TEXT);
                ''')
            self.checkpoint_connection = sqlite3.connect(self.path, check_same_thread=False)
            self._stack.callback(self.checkpoint_connection.close)
        except (OSError, sqlite3.Error, SessionPersistenceError) as error:
            logger.error('session_storage_open_failed error_type=%s', type(error).__name__)
            self.close()
            raise SessionPersistenceError('storage_unavailable') from error

    @staticmethod
    def _check_private(path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
            raise SessionPersistenceError('unsafe_store_file')

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
        now = self._now()
        self._write('''INSERT INTO naiyue_sessions
            (session_id,thread_id,model_id,workspace_root,state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)''', (session_id, session['thread_id'], session['model_id'],
            str(session['workspace_root']) if session['workspace_root'] is not None else None,
            'ready', now, now))

    def get(self, session_id):
        rows = self._read('SELECT * FROM naiyue_sessions WHERE session_id=?', (session_id,))
        if not rows:
            raise SessionPersistenceError('session_not_found')
        row = rows[0]
        if row['state'] not in STATES or not row['thread_id'] or not row['model_id']:
            raise SessionPersistenceError('invalid_session_metadata')
        return row

    def list_sessions(self):
        return self._read('SELECT * FROM naiyue_sessions ORDER BY updated_at DESC, session_id ASC')

    def select_model(self, session_id, model_id):
        self._write('UPDATE naiyue_sessions SET model_id=?,updated_at=? WHERE session_id=?',
                    (model_id, self._now(), session_id))

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
