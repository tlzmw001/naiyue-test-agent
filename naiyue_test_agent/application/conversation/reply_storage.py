"""Reply boundaries are necessary saved facts, not a reconstruction from events."""

from naiyue_test_agent.storage.queries import read_rows, session_row

import json
import sqlite3
import logging
from datetime import datetime, timezone
from naiyue_test_agent.application.errors import SessionPersistenceError

logger = logging.getLogger(__name__)


def initialize(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 11:
        return
    with connection:
        connection.execute('BEGIN')
        connection.execute('ALTER TABLE naiyue_chat_messages ADD COLUMN reply_id TEXT')
        connection.execute('''CREATE TABLE naiyue_reply_boundaries(
            session_id TEXT NOT NULL, thread_id TEXT NOT NULL, reply_id TEXT NOT NULL,
            input_message_id TEXT NOT NULL, source_checkpoint_id TEXT, input_sequence INTEGER NOT NULL,
            PRIMARY KEY(session_id,thread_id,reply_id))''')
        connection.execute('UPDATE naiyue_store_info SET version=11')


def save_boundaries(connection, session_id, update):
    for boundary in update.boundaries:
        row = connection.execute('''SELECT sequence FROM naiyue_chat_messages
            WHERE session_id=? AND thread_id=? AND message_id=?''',
            (session_id, update.thread_id, boundary['input_message_id'])).fetchone()
        if row:
            connection.execute('''INSERT OR IGNORE INTO naiyue_reply_boundaries
                (session_id,thread_id,reply_id,input_message_id,source_checkpoint_id,input_sequence)
                VALUES(?,?,?,?,?,?)''', (session_id, update.thread_id, boundary['reply_id'],
                boundary['input_message_id'], boundary['source_checkpoint_id'], row['sequence']))
            connection.execute('UPDATE naiyue_reply_boundaries SET source_thread_id=thread_id WHERE session_id=? AND thread_id=? AND source_thread_id IS NULL', (session_id, update.thread_id))


def latest(connection, session_id):
    row = session_row(connection, session_id)
    rows = read_rows(connection, '''SELECT * FROM naiyue_reply_boundaries
        WHERE session_id=? AND thread_id=? ORDER BY input_sequence DESC LIMIT 1''',
        (session_id, row['thread_id']))
    return rows[0] if rows else None


def initialize_regeneration(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 12:
        return
    with connection:
        connection.execute('BEGIN')
        connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN conversation_revision INTEGER NOT NULL DEFAULT 0')
        connection.execute('ALTER TABLE naiyue_chat_messages ADD COLUMN source_thread_id TEXT')
        connection.execute('UPDATE naiyue_chat_messages SET source_thread_id=thread_id')
        connection.execute('ALTER TABLE naiyue_reply_boundaries ADD COLUMN source_thread_id TEXT')
        connection.execute('UPDATE naiyue_reply_boundaries SET source_thread_id=thread_id')
        connection.execute('''CREATE TABLE naiyue_regenerations(
            session_id TEXT NOT NULL,request_id TEXT NOT NULL,reply_id TEXT NOT NULL,revision INTEGER NOT NULL,
            old_thread_id TEXT NOT NULL,new_thread_id TEXT NOT NULL,run_id TEXT NOT NULL,status TEXT NOT NULL,
            obsolete_children TEXT NOT NULL DEFAULT '[]',PRIMARY KEY(session_id,request_id))''')
        connection.execute('UPDATE naiyue_store_info SET version=12')


def action(connection, session_id, request_id):
    rows = read_rows(connection, 'SELECT * FROM naiyue_regenerations WHERE session_id=? AND request_id=?', (session_id, request_id))
    return rows[0] if rows else None


def record_action(connection, session_id, request_id, reply, revision, new_thread, run_id):
    connection.execute('INSERT INTO naiyue_regenerations VALUES(?,?,?,?,?,?,?,?,?)',
        (session_id, request_id, reply['reply_id'], revision, reply['thread_id'], new_thread, run_id, 'preparing', '[]'))


def activate(connection, session_id, request_id, *, checkpoint, head_checkpoint, boundary):
    from .chat_queries import subagent_records
    try:
        op = action(connection, session_id, request_id)
        row = session_row(connection, session_id)
        if (row['state'] != 'ready' or row['thread_id'] != op['old_thread_id']
                or row['conversation_revision'] != op['revision']):
            raise SessionPersistenceError('regeneration_stale')
        old, new = op['old_thread_id'], op['new_thread_id']
        children_before = {r['agent_id'] for r in subagent_records(connection, session_id, old)}
        head = read_rows(connection, 'SELECT * FROM naiyue_chat_sync WHERE session_id=? AND thread_id=?', (session_id, old))[0]
        connection.execute('DELETE FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND sequence>=?',
                                   (session_id, old, boundary['input_sequence']))
        connection.execute('UPDATE naiyue_chat_messages SET thread_id=? WHERE session_id=? AND thread_id=?', (new, session_id, old))
        connection.execute('DELETE FROM naiyue_reply_boundaries WHERE session_id=? AND thread_id=? AND input_sequence>=?',
                                   (session_id, old, boundary['input_sequence']))
        connection.execute('UPDATE naiyue_reply_boundaries SET thread_id=? WHERE session_id=? AND thread_id=?', (new, session_id, old))
        connection.execute('DELETE FROM naiyue_chat_sync WHERE session_id=? AND thread_id=?', (session_id, old))
        connection.execute('INSERT INTO naiyue_chat_sync VALUES(?,?,?,?,?,?,?,?)',
            (session_id, new, '', 'root', head_checkpoint, head['revision'] + 1, head['history_version'] + 1, False))
        retained = {r['agent_id'] for r in subagent_records(connection, session_id, new)}
        obsolete = ['subagent:' + identity for identity in children_before - retained]
        connection.execute('''UPDATE naiyue_subagent_instances
            SET state='retired',version=version+1,updated_at=?
            WHERE session_id=? AND last_parent_reply_id=? AND state!='retired' ''',
            (datetime.now(timezone.utc).isoformat(), session_id, boundary['reply_id']))
        for thread in obsolete:
            connection.execute('''UPDATE naiyue_subagent_instances SET checkpoint_id=NULL
                WHERE session_id=? AND child_thread_id=? AND state='retired' ''', (session_id, thread))
            for sql in ('DELETE FROM naiyue_chat_messages WHERE session_id=? AND thread_id=?',
                        'DELETE FROM naiyue_chat_sync WHERE session_id=? AND thread_id=?',
                        'DELETE FROM naiyue_reply_boundaries WHERE session_id=? AND thread_id=?'):
                connection.execute(sql, (session_id, thread))
        connection.execute('''UPDATE naiyue_sessions SET thread_id=?,conversation_revision=conversation_revision+1,
            checkpoint_id=?,checkpoint_ns='',state='running',run_id=? WHERE session_id=?''',
            (new, checkpoint, op['run_id'], session_id))
        connection.execute("UPDATE naiyue_regenerations SET status='activated',obsolete_children=? WHERE session_id=? AND request_id=?",
            (json.dumps(obsolete), session_id, request_id))
    except sqlite3.Error as error:
        logger.error('regeneration_save_failed error_type=%s', type(error).__name__)
        raise SessionPersistenceError('session_save_unconfirmed') from error


def finish_actions(connection, session_id, thread_id, status):
    connection.execute('''UPDATE naiyue_regenerations SET status=? WHERE session_id=? AND new_thread_id=?
        AND status IN ('activated','interrupted')''', (status, session_id, thread_id))


def all_actions(connection, session_id):
    return read_rows(connection, 'SELECT * FROM naiyue_regenerations WHERE session_id=?', (session_id,))


def fail_preparation(connection, session_id, request_id):
    connection.execute("UPDATE naiyue_regenerations SET status='prepare_failed' WHERE session_id=? AND request_id=? AND status='preparing'",
                               (session_id, request_id))
