"""Bounded archived conversation reads and atomic chat updates."""

import logging
import sqlite3

from naiyue_test_agent.application.errors import SessionPersistenceError
from .queries import read_rows, session_row

from .chat_writes import write_chat_updates
from .queries import subagent_references
from naiyue_test_agent.application.conversation import chat_queries


logger = logging.getLogger(__name__)


class ConversationRepo:
    def __init__(self, database):
        self.database = database

    def chat_heads(self, session_id):
        with self.database.transaction(write=False, error_code='session_read_failed') as connection:
            return read_rows(connection, 'SELECT * FROM naiyue_chat_sync WHERE session_id=?', (session_id,))

    def save_chat_updates(self, session_id, updates):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            try:
                session_row(connection, session_id)
                write_chat_updates(connection, session_id, updates)
            except sqlite3.Error as error:
                logger.error('chat_history_write_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('session_save_unconfirmed') from error

    def read_chat_page(self, session_id, thread_id, **query):
        with self.database.transaction(write=False, error_code='session_read_failed') as connection:
            return chat_queries.read_page(connection, session_id, thread_id, **query)

    def read_history_tool(self, source_session_id, project_id, request, settings):
        from naiyue_test_agent.application.conversation.history_queries import query
        try:
            with self.database.transaction() as connection:
                return query(connection, source_session_id, project_id, request, settings)
        except sqlite3.Error as error:
            logger.error('history_tool_read_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('history_read_failed') from error

    def read_chat_message(self, session_id, thread_id, sequence, **query):
        with self.database.transaction(write=False, error_code='session_read_failed') as connection:
            return chat_queries.read_message(connection, session_id, thread_id, sequence, **query)

    def read_approval_seed(self, session_id, thread_id, batch_id):
        with self.database.transaction(write=False, error_code='session_read_failed') as connection:
            return chat_queries.approval_seed(connection, session_id, thread_id, batch_id)

    def chat_subagents(self, session_id):
        with self.database.transaction(write=False, error_code='session_read_failed') as connection:
            from naiyue_test_agent.application.subagents.subagent_history import merge_registered_subagents
            records = chat_queries.subagent_records(connection, session_id, session_row(connection, session_id)['thread_id'])
            return merge_registered_subagents(records, subagent_references(connection, session_id))

    def source_reference(self, workspace_id, row, *, detail):
        from naiyue_test_agent.application.memory.memory_results import source_reference
        with self.database.transaction(error_code='session_read_failed') as connection:
            return source_reference(connection, workspace_id, row, detail=detail)

    def upper_sequence(self, session_id, thread_id):
        with self.database.transaction(error_code='session_read_failed') as connection:
            return connection.execute("SELECT COALESCE(MAX(sequence),0) FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND checkpoint_ns=''", (session_id, thread_id)).fetchone()[0]

    def first_human(self, session_id, thread_id):
        return self.database.read_rows("SELECT body_json FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND message_type='human' AND kind='message' ORDER BY sequence LIMIT 1", (session_id, thread_id))

    def reply_inputs(self, session_id, thread_id, reply_id):
        return self.database.read_rows("SELECT body_json FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND reply_id=? AND message_type='human' ORDER BY sequence", (session_id, thread_id, reply_id))

    def reasoning_body(self, session_id, thread, sequence, revision, max_bytes):
        return self.database.read_rows("""SELECT revision,
            CASE WHEN length(CAST(body_json AS BLOB))<=? THEN body_json END AS body
            FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND sequence=? AND message_type='ai'
            AND (SELECT conversation_revision FROM naiyue_sessions WHERE session_id=?)=?""",
            (max_bytes, session_id, thread, sequence, session_id, revision))

    def file_change_body(self, session_id, thread, sequence, revision, max_bytes):
        return self.database.read_rows("""SELECT revision,
            CASE WHEN length(CAST(json_extract(body_json,'$.data.artifact') AS BLOB))<=?
            THEN json_extract(body_json,'$.data.artifact') END AS artifact
            FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND sequence=? AND message_type='tool'
            AND (SELECT conversation_revision FROM naiyue_sessions WHERE session_id=?)=?""",
            (max_bytes, session_id, thread, sequence, session_id, revision))
