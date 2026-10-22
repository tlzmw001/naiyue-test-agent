"""One instance row, written through the existing SessionStorage connection."""

import logging
import sqlite3

from .errors import SessionPersistenceError


logger = logging.getLogger(__name__)
FIELDS = ('session_id', 'agent_id', 'agent_path', 'child_thread_id', 'agent', 'role_fingerprint',
          'model_id', 'last_task', 'state', 'version', 'checkpoint_ns', 'checkpoint_id',
          'last_delegation_id', 'last_parent_run_id', 'last_parent_batch_id',
          'last_parent_tool_call_id', 'last_parent_reply_id', 'created_at', 'updated_at')


def initialize(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 14:
        return
    with connection:
        connection.execute('BEGIN')
        connection.execute('''CREATE TABLE naiyue_subagent_instances(
            session_id TEXT NOT NULL, agent_id TEXT NOT NULL, agent_path TEXT NOT NULL,
            child_thread_id TEXT NOT NULL UNIQUE, agent TEXT NOT NULL,
            role_fingerprint TEXT NOT NULL, model_id TEXT NOT NULL, last_task TEXT NOT NULL,
            state TEXT NOT NULL, version INTEGER NOT NULL, checkpoint_ns TEXT NOT NULL,
            checkpoint_id TEXT, last_delegation_id TEXT NOT NULL, last_parent_run_id TEXT NOT NULL,
            last_parent_batch_id TEXT NOT NULL, last_parent_tool_call_id TEXT NOT NULL,
            last_parent_reply_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(session_id,agent_id))''')
        connection.execute('UPDATE naiyue_store_info SET version=14')


def save(storage, record, expected_version):
    """Compare-and-swap a claimed or settled row; never silently replace an owner."""
    try:
        with storage._lock, storage.connection:
            storage.connection.execute('BEGIN IMMEDIATE')
            storage.get(record['session_id'])
            if expected_version is None:
                storage.connection.execute('INSERT INTO naiyue_subagent_instances (' + ','.join(FIELDS)
                    + ') VALUES(' + ','.join('?' for _ in FIELDS) + ')', tuple(record[key] for key in FIELDS))
            else:
                mutable = FIELDS[2:]
                cursor = storage.connection.execute('UPDATE naiyue_subagent_instances SET '
                    + ','.join(key + '=?' for key in mutable) + ' WHERE session_id=? AND agent_id=? AND version=?',
                    (*[record[key] for key in mutable], record['session_id'], record['agent_id'], expected_version))
                if cursor.rowcount != 1:
                    raise SessionPersistenceError('subagent_record_changed')
    except sqlite3.Error as error:
        logger.error('subagent_record_write_failed error_type=%s', type(error).__name__)
        raise SessionPersistenceError('subagent_save_unconfirmed') from error


def cancel_claim(storage, record, previous):
    if previous is not None:
        restored = {**previous, 'version': record['version'] + 1, 'updated_at': storage._now()}
        return save(storage, restored, record['version'])
    try:
        with storage._lock, storage.connection:
            cursor = storage.connection.execute('''DELETE FROM naiyue_subagent_instances
                WHERE session_id=? AND agent_id=? AND version=? AND state='running' ''',
                (record['session_id'], record['agent_id'], record['version']))
            if cursor.rowcount != 1:
                raise SessionPersistenceError('subagent_record_changed')
    except sqlite3.Error as error:
        logger.error('subagent_claim_release_failed error_type=%s', type(error).__name__)
        raise SessionPersistenceError('subagent_save_unconfirmed') from error


def finish_parent(storage, session_id, run_id):
    """Caller owns the parent state/archive transaction."""
    rows = storage._read('''SELECT * FROM naiyue_subagent_instances
        WHERE session_id=? AND last_parent_run_id=? AND state='pending_parent' ''', (session_id, run_id))
    for row in rows:
        head = storage.connection.execute('''SELECT checkpoint_id FROM naiyue_chat_sync
            WHERE session_id=? AND thread_id=? AND checkpoint_ns=?''',
            (session_id, row['child_thread_id'], row['checkpoint_ns'])).fetchone()
        latest = storage.connection.execute('''SELECT checkpoint_id FROM checkpoints
            WHERE thread_id=? AND checkpoint_ns=? ORDER BY checkpoint_id DESC LIMIT 1''',
            (row['child_thread_id'], row['checkpoint_ns'])).fetchone()
        if not head or not latest or head[0] != row['checkpoint_id'] or latest[0] != row['checkpoint_id']:
            raise SessionPersistenceError('subagent_checkpoint_unconfirmed')
        storage.connection.execute('''UPDATE naiyue_subagent_instances SET state='ready',version=version+1,updated_at=?
            WHERE session_id=? AND agent_id=? AND version=?''',
            (storage._now(), session_id, row['agent_id'], row['version']))
