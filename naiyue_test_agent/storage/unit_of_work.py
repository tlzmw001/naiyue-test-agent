"""Required multi-aggregate saving and deletion on one connection."""

import json
import logging
import sqlite3

from naiyue_test_agent.application.errors import SessionPersistenceError
from .queries import read_rows, utc_now, session_row

from .queries import STATES
from .chat_writes import write_chat_updates
from naiyue_test_agent.application.conversation import reply_storage
from naiyue_test_agent.application.subagents import subagent_storage


logger = logging.getLogger(__name__)


class RunUnitOfWork:
    def __init__(self, database):
        self.database = database

    def finish_run(self, session_id, state, checkpoint_id, checkpoint_ns, last_run_status, chat_updates=(), *, subagent_run_id=None):
        with self.database.transaction(write=True, error_code='session_save_unconfirmed') as connection:
            if state not in STATES:
                raise SessionPersistenceError('invalid_session_state')
            try:
                session_row(connection, session_id)
                write_chat_updates(connection, session_id, chat_updates)
                if subagent_run_id is not None:
                    if session_row(connection, session_id)['run_id'] != subagent_run_id:
                        raise SessionPersistenceError('subagent_parent_run_changed')
                    subagent_storage.finish_parent(connection, session_id, subagent_run_id)
                reply_storage.finish_actions(connection, session_id, session_row(connection, session_id)['thread_id'], last_run_status)
                connection.execute('''UPDATE naiyue_sessions SET state=?,checkpoint_id=?,checkpoint_ns=?,
                    last_run_status=?,updated_at=? WHERE session_id=?''',
                    (state, checkpoint_id, checkpoint_ns, last_run_status, utc_now(), session_id))
            except sqlite3.Error as error:
                logger.error('session_storage_write_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('session_save_unconfirmed') from error

    def delete(self, session_id, child_threads):
        with self.database.transaction(write=True, error_code='session_delete_unconfirmed') as connection:
            try:
                row = session_row(connection, session_id)
                operations = reply_storage.all_actions(connection, session_id)
                extra = {key for op in operations for key in (op['old_thread_id'], op['new_thread_id'], *json.loads(op['obsolete_children']))}
                registered = read_rows(connection, 'SELECT child_thread_id FROM naiyue_subagent_instances WHERE session_id=?', (session_id,))
                threads = [(key,) for key in {row['thread_id'], *child_threads, *extra, *(item['child_thread_id'] for item in registered)}]
                # One transaction across the current official SQLite tables and our metadata.
                connection.executemany('DELETE FROM writes WHERE thread_id=?', threads)
                connection.executemany('DELETE FROM checkpoints WHERE thread_id=?', threads)
                connection.execute('DELETE FROM naiyue_chat_messages WHERE session_id=?', (session_id,))
                connection.execute('DELETE FROM naiyue_reply_boundaries WHERE session_id=?', (session_id,))
                connection.execute('DELETE FROM naiyue_chat_sync WHERE session_id=?', (session_id,))
                connection.execute('DELETE FROM naiyue_model_invocations WHERE session_id=?', (session_id,))
                connection.execute('DELETE FROM naiyue_model_usage_runs WHERE session_id=?', (session_id,))
                connection.execute('DELETE FROM naiyue_diagnostic_events WHERE session_id=?', (session_id,))
                connection.execute('DELETE FROM naiyue_diagnostic_runs WHERE session_id=?', (session_id,))
                connection.execute('DELETE FROM naiyue_regenerations WHERE session_id=?', (session_id,))
                connection.execute('DELETE FROM naiyue_subagent_instances WHERE session_id=?', (session_id,))
                from naiyue_test_agent.application.memory.memory_overview_store import purge
                purge(connection,session_id)
                connection.execute('DELETE FROM naiyue_sessions WHERE session_id=?', (session_id,))
            except sqlite3.Error as error:
                logger.error('session_delete_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('session_delete_unconfirmed') from error
