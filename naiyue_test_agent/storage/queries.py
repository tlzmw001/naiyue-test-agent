"""Connection-bound SQL shared by repositories; never owns a transaction."""

import logging
import sqlite3
from datetime import datetime, timezone
from naiyue_test_agent.application.errors import SessionPersistenceError

logger = logging.getLogger(__name__)
STATES = {'ready', 'running', 'failed', 'interrupted', 'review_required'}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def read_rows(connection, sql, values=()):
    try:
        return [dict(row) for row in connection.execute(sql, values).fetchall()]
    except sqlite3.Error as error:
        logger.error('session_storage_read_failed error_type=%s', type(error).__name__)
        raise SessionPersistenceError('session_read_failed') from error


def write_one(connection, sql, values):
    try:
        cursor = connection.execute(sql, values)
        if cursor.rowcount != 1:
            raise SessionPersistenceError('session_not_found')
    except sqlite3.Error as error:
        logger.error('session_storage_write_failed error_type=%s', type(error).__name__)
        raise SessionPersistenceError('session_save_unconfirmed') from error


def session_row(connection, session_id):
    rows = read_rows(connection, 'SELECT * FROM naiyue_sessions WHERE session_id=?', (session_id,))
    if not rows:
        raise SessionPersistenceError('session_not_found')
    row = rows[0]
    if row['state'] not in STATES or not row['thread_id'] or not row['model_id']:
        raise SessionPersistenceError('invalid_session_metadata')
    return row


def workspace_binding(connection):
    rows = read_rows(connection, 'SELECT workspace_id,root_path FROM naiyue_workspace')
    if len(rows) > 1:
        raise SessionPersistenceError('invalid_workspace_binding')
    return rows[0] if rows else None


def subagent_row(connection, session_id, agent_id):
    rows = read_rows(connection, 'SELECT * FROM naiyue_subagent_instances WHERE session_id=? AND agent_id=?', (session_id, agent_id))
    return rows[0] if rows else None


def subagent_references(connection, session_id):
    return {row['agent_id']: row for row in read_rows(connection,
        'SELECT * FROM naiyue_subagent_instances WHERE session_id=?', (session_id,))}


def chat_heads(connection, session_id):
    return read_rows(connection, 'SELECT * FROM naiyue_chat_sync WHERE session_id=?', (session_id,))
