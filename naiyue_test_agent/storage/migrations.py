"""Existing transaction steps, gated by a validated snapshot under instance ownership."""

import logging

from .backup import create_backup
from .backup_schema import CURRENT_SCHEMA, SUPPORTED_SCHEMAS, read_schema_version
from naiyue_test_agent.application.errors import StorageStartupError
from naiyue_test_agent.application.conversation import chat_queries, reply_storage
from naiyue_test_agent.application.observability import model_usage_storage, diagnostic_storage
from naiyue_test_agent.application.subagents import subagent_storage
from naiyue_test_agent.application.memory import memory_schema, memory_lifecycle_schema, memory_overview_store
from . import execution_storage


logger = logging.getLogger(__name__)


def _failure(connection, database_path, code, phase, source, backup_id, original):
    current = None
    rolled_back = True
    try:
        if connection.in_transaction:
            connection.rollback()
    except BaseException as error:
        rolled_back = False
        logger.error('migration_rollback_failed phase=%s error_type=%s', phase, type(error).__name__)
    if rolled_back:
        try:
            current = read_schema_version(connection)
        except Exception as error:
            logger.error('migration_version_unavailable error_type=%s', type(error).__name__)
    logger.error('storage_startup_failed phase=%s source_schema=%s current_schema=%s target_schema=%s backup_id=%s error_type=%s',
                 phase, source, current, CURRENT_SCHEMA, backup_id, type(original).__name__)
    raise StorageStartupError(code, database_path=database_path, phase=phase, source_schema=source,
        current_schema=current, target_schema=CURRENT_SCHEMA, backup_id=backup_id) from original


def _migrate_session_schema(connection):
    source = read_schema_version(connection)
    if source not in (4, 5):
        return
    with connection:
        connection.execute('BEGIN')
        if source == 4:
            connection.execute("ALTER TABLE naiyue_sessions ADD COLUMN thinking_level TEXT NOT NULL DEFAULT 'model_default'")
        connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN title TEXT')
        connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0')
        connection.execute('ALTER TABLE naiyue_sessions ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0')
        rows = connection.execute('SELECT session_id FROM naiyue_sessions ORDER BY updated_at DESC,session_id').fetchall()
        connection.executemany('UPDATE naiyue_sessions SET sort_order=? WHERE session_id=?',
                               [(index, row[0]) for index, row in enumerate(rows)])
        connection.execute('UPDATE naiyue_store_info SET version=6')


def _migrate_chat_schema(connection):
    if read_schema_version(connection) >= 7:
        return
    with connection:
        connection.execute('BEGIN')
        connection.execute('''CREATE TABLE naiyue_chat_sync(
            session_id TEXT NOT NULL, thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL,
            agent_path TEXT NOT NULL, checkpoint_id TEXT NOT NULL, revision INTEGER NOT NULL,
            PRIMARY KEY(session_id,thread_id,checkpoint_ns))''')
        connection.execute('''CREATE TABLE naiyue_chat_messages(
            session_id TEXT NOT NULL, thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL,
            message_id TEXT NOT NULL, sequence INTEGER NOT NULL, revision INTEGER NOT NULL,
            body_json TEXT NOT NULL, body_hash TEXT NOT NULL, kind TEXT NOT NULL,
            source_checkpoint_id TEXT NOT NULL,
            PRIMARY KEY(session_id,thread_id,checkpoint_ns,message_id),
            UNIQUE(session_id,thread_id,checkpoint_ns,sequence))''')
        connection.execute('UPDATE naiyue_store_info SET version=7')


def initialize_store(connection, database_path, *, existing):
    source, backup_id = None, None
    if existing:
        try:
            source = read_schema_version(connection)
            if source not in SUPPORTED_SCHEMAS:
                raise ValueError('unsupported_store_schema')
        except Exception as error:
            _failure(connection, database_path, 'unsupported_store_schema', 'source_validation', source, None, error)
        if source < CURRENT_SCHEMA:
            try:
                manifest = create_backup(connection, database_path, target_schema=CURRENT_SCHEMA)
                backup_id = manifest['backup_id']
            except Exception as error:
                _failure(connection, database_path, 'backup_failed', 'backup', source, None, error)
    else:
        connection.executescript('''
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
    steps = ((6, _migrate_session_schema), (7, _migrate_chat_schema),
        (8, chat_queries.migrate_chat_queries), (9, model_usage_storage.initialize),
        (10, diagnostic_storage.initialize), (11, reply_storage.initialize),
        (12, reply_storage.initialize_regeneration), (13, model_usage_storage.migrate_duration),
        (14, subagent_storage.initialize), (15, memory_schema.initialize_project), (16, memory_lifecycle_schema.initialize_project), (17, memory_overview_store.initialize_project), (18, execution_storage.initialize))
    for target, step in steps:
        current = None
        try:
            current = read_schema_version(connection)
            step(connection)
        except Exception as error:
            if not existing or source >= CURRENT_SCHEMA or current is not None and current >= target:
                raise
            _failure(connection, database_path, 'migration_failed', f'schema_{target}', source, backup_id, error)
