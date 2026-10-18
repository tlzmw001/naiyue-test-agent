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
from . import chat_queries, model_usage_storage, diagnostic_storage, reply_storage


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
                if len(version) != 1 or version[0][0] not in (4, 5, 6, 7, 8, 9, 10, 11, 12):
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
            self._migrate_chat_schema()
            chat_queries.migrate_chat_queries(self.connection)
            model_usage_storage.initialize(self.connection)
            diagnostic_storage.initialize(self.connection)
            reply_storage.initialize(self.connection)
            reply_storage.initialize_regeneration(self.connection)
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

    def _migrate_chat_schema(self):
        if self.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 7:
            return
        with self.connection:
            self.connection.execute('BEGIN')
            self.connection.execute('''CREATE TABLE naiyue_chat_sync(
                session_id TEXT NOT NULL, thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL,
                agent_path TEXT NOT NULL, checkpoint_id TEXT NOT NULL, revision INTEGER NOT NULL,
                PRIMARY KEY(session_id,thread_id,checkpoint_ns))''')
            self.connection.execute('''CREATE TABLE naiyue_chat_messages(
                session_id TEXT NOT NULL, thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL,
                message_id TEXT NOT NULL, sequence INTEGER NOT NULL, revision INTEGER NOT NULL,
                body_json TEXT NOT NULL, body_hash TEXT NOT NULL, kind TEXT NOT NULL,
                source_checkpoint_id TEXT NOT NULL,
                PRIMARY KEY(session_id,thread_id,checkpoint_ns,message_id),
                UNIQUE(session_id,thread_id,checkpoint_ns,sequence))''')
            self.connection.execute('UPDATE naiyue_store_info SET version=7')

    def save_model_usage_event(self, event):
        with self._lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            model_usage_storage.save_event(self.connection, event)

    def finish_model_usage_run(self, session_id, run_id, **summary):
        with self._lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            return model_usage_storage.finish_run(self.connection, session_id, run_id, **summary)

    def read_model_usage(self, session_id, *, model_limit, after_model=None):
        try:
            with self._lock, self.connection:
                self.connection.execute('BEGIN')
                return model_usage_storage.read_summary(self.connection, session_id,
                    model_limit=model_limit, after_model=after_model)
        except sqlite3.Error as error:
            logger.error('model_usage_read_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('usage_read_failed') from error

    def chat_heads(self, session_id):
        return self._read('SELECT * FROM naiyue_chat_sync WHERE session_id=?', (session_id,))

    def prepare_diagnostics(self, settings):
        with self._lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            return diagnostic_storage.prepare(self.connection, settings)

    def save_diagnostic_event(self, record, settings):
        with self._lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            return diagnostic_storage.save_event(self.connection, record, settings)

    def finish_diagnostic_run(self, record, settings, **summary):
        with self._lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            return diagnostic_storage.finish_run(self.connection, record, settings, **summary)

    def prune_diagnostics(self, settings, *, now=None):
        with self._lock, self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            return diagnostic_storage.prune(self.connection, settings, now=now)

    def read_diagnostic_runs(self, session_id, *, limit=50, before=None):
        with self._lock, self.connection:
            self.connection.execute('BEGIN')
            return diagnostic_storage.read_runs(self.connection, session_id, limit=limit, before=before)

    def read_diagnostic_events(self, session_id, run_id, *, limit=50, after=0):
        with self._lock, self.connection:
            self.connection.execute('BEGIN')
            return diagnostic_storage.read_events(self.connection, session_id, run_id, limit=limit, after=after)

    def _write_chat_updates(self, session_id, updates):
        for update in updates:
            scope = (session_id, update.thread_id, update.checkpoint_ns)
            head = self.connection.execute('''SELECT revision,history_version FROM naiyue_chat_sync
                WHERE session_id=? AND thread_id=? AND checkpoint_ns=?''', scope).fetchone()
            if (head['revision'] if head else 0) != update.expected_revision:
                raise SessionPersistenceError('chat_history_stale')
            history_version = head['history_version'] if head else 0
            sequence = self.connection.execute('''SELECT COALESCE(MAX(sequence),0) FROM naiyue_chat_messages
                WHERE session_id=? AND thread_id=? AND checkpoint_ns=?''', scope).fetchone()[0]
            for message in update.messages:
                key = (*scope, message['message_id'])
                saved = self.connection.execute('''SELECT body_hash,parent_message_id FROM naiyue_chat_messages
                    WHERE session_id=? AND thread_id=? AND checkpoint_ns=? AND message_id=?''', key).fetchone()
                if saved and saved['body_hash'] == message['body_hash'] and saved['parent_message_id'] == message.get('parent_message_id'):
                    continue
                if saved:
                    history_version += 1
                    self.connection.execute('''UPDATE naiyue_chat_messages SET body_json=?,body_hash=?,kind=?,
                        source_checkpoint_id=?,parent_message_id=?,message_type=?,reply_id=?,revision=revision+1
                        WHERE session_id=? AND thread_id=? AND checkpoint_ns=? AND message_id=?''',
                        (message['body_json'], message['body_hash'], message['kind'], message['source_checkpoint_id'],
                         message.get('parent_message_id'), message.get('message_type'), message.get('reply_id'), *key))
                else:
                    sequence += 1
                    self.connection.execute('''INSERT INTO naiyue_chat_messages
                        (session_id,thread_id,checkpoint_ns,message_id,sequence,revision,body_json,body_hash,kind,
                         source_checkpoint_id,message_type,parent_message_id,reply_id,source_thread_id) VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,?)''',
                        (*key, sequence, message['body_json'], message['body_hash'], message['kind'], message['source_checkpoint_id'],
                         message.get('message_type'), message.get('parent_message_id'), message.get('reply_id'), update.thread_id))
            reply_storage.save_boundaries(self.connection, session_id, update)
            self.connection.execute('''INSERT INTO naiyue_chat_sync VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id,thread_id,checkpoint_ns) DO UPDATE SET
                checkpoint_id=excluded.checkpoint_id,revision=excluded.revision,
                history_version=excluded.history_version,stopped=excluded.stopped''',
                (*scope, update.agent_path, update.checkpoint_id, update.expected_revision + 1, history_version, update.stopped))

    def save_chat_updates(self, session_id, updates):
        try:
            with self._lock, self.connection:
                self.connection.execute('BEGIN IMMEDIATE')
                self.get(session_id)
                self._write_chat_updates(session_id, updates)
        except sqlite3.Error as error:
            logger.error('chat_history_write_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_save_unconfirmed') from error

    def read_chat_page(self, session_id, thread_id, **query):
        with self._lock:
            return chat_queries.read_page(self, session_id, thread_id, **query)

    def read_chat_message(self, session_id, thread_id, sequence, **query):
        with self._lock:
            return chat_queries.read_message(self, session_id, thread_id, sequence, **query)

    def read_approval_seed(self, session_id, thread_id, batch_id):
        with self._lock:
            return chat_queries.approval_seed(self, session_id, thread_id, batch_id)

    def chat_subagents(self, session_id):
        with self._lock:
            return chat_queries.subagent_records(self, session_id, self.get(session_id)['thread_id'])

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

    def list_workspace_sessions(self, workspace_root):
        root = str(workspace_root) if workspace_root is not None else None
        return self._read('''SELECT session_id,model_id,workspace_root,state,updated_at,last_run_status,title,pinned
            FROM naiyue_sessions WHERE workspace_root IS ?
            ORDER BY pinned DESC,sort_order ASC,created_at DESC,session_id ASC''', (root,))

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

    def ensure_title(self, session_id, title):
        try:
            with self._lock, self.connection:
                self.connection.execute('UPDATE naiyue_sessions SET title=? WHERE session_id=? AND title IS NULL',
                                        (title, session_id))
        except sqlite3.Error as error:
            logger.error('session_title_write_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_title_unavailable') from error

    def reorder(self, workspace_root, identities):
        root = str(workspace_root) if workspace_root is not None else None
        try:
            with self._lock, self.connection:
                self.connection.execute('BEGIN IMMEDIATE')
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
                self.connection.execute('BEGIN IMMEDIATE')
                row = self.get(session_id)
                operations = reply_storage.all_actions(self, session_id)
                extra = {key for op in operations for key in (op['old_thread_id'], op['new_thread_id'], *json.loads(op['obsolete_children']))}
                threads = [(key,) for key in {row['thread_id'], *child_threads, *extra}]
                # One transaction across the current official SQLite tables and our metadata.
                self.connection.executemany('DELETE FROM writes WHERE thread_id=?', threads)
                self.connection.executemany('DELETE FROM checkpoints WHERE thread_id=?', threads)
                self.connection.execute('DELETE FROM naiyue_chat_messages WHERE session_id=?', (session_id,))
                self.connection.execute('DELETE FROM naiyue_reply_boundaries WHERE session_id=?', (session_id,))
                self.connection.execute('DELETE FROM naiyue_chat_sync WHERE session_id=?', (session_id,))
                self.connection.execute('DELETE FROM naiyue_model_invocations WHERE session_id=?', (session_id,))
                self.connection.execute('DELETE FROM naiyue_model_usage_runs WHERE session_id=?', (session_id,))
                self.connection.execute('DELETE FROM naiyue_diagnostic_events WHERE session_id=?', (session_id,))
                self.connection.execute('DELETE FROM naiyue_diagnostic_runs WHERE session_id=?', (session_id,))
                self.connection.execute('DELETE FROM naiyue_regenerations WHERE session_id=?', (session_id,))
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

    def finish_run(self, session_id, state, checkpoint_id, checkpoint_ns, last_run_status, chat_updates=()):
        if state not in STATES:
            raise SessionPersistenceError('invalid_session_state')
        try:
            with self._lock, self.connection:
                self.connection.execute('BEGIN IMMEDIATE')
                self.get(session_id)
                self._write_chat_updates(session_id, chat_updates)
                reply_storage.finish_actions(self.connection, session_id, self.get(session_id)['thread_id'], last_run_status)
                self.connection.execute('''UPDATE naiyue_sessions SET state=?,checkpoint_id=?,checkpoint_ns=?,
                    last_run_status=?,updated_at=? WHERE session_id=?''',
                    (state, checkpoint_id, checkpoint_ns, last_run_status, self._now(), session_id))
        except sqlite3.Error as error:
            logger.error('session_storage_write_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_save_unconfirmed') from error

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
