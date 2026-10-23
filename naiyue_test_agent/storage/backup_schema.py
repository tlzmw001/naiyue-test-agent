"""Read-only snapshot structure checks; never deserialize checkpoint payloads."""

from contextlib import closing
from pathlib import Path
import sqlite3
from uuid import UUID


CURRENT_SCHEMA = 18
SUPPORTED_SCHEMAS = frozenset(range(4, CURRENT_SCHEMA + 1))


def read_schema_version(connection):
    versions = connection.execute('SELECT version FROM naiyue_store_info').fetchall()
    if len(versions) != 1 or type(versions[0][0]) is not int:
        raise ValueError('unsupported_store_schema')
    return versions[0][0]


def check_snapshot(path):
    # Caller has checked a closed standalone file and absence of sidecars.
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
        connection.execute('PRAGMA trusted_schema=OFF')
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('backup_invalid')
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('backup_invalid')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = read_schema_version(connection)
        if version not in SUPPORTED_SCHEMAS:
            raise ValueError('unsupported_store_schema')
        required = {
            'naiyue_store_info': 'version',
            'naiyue_workspace': 'singleton workspace_id root_path',
            'naiyue_sessions': 'session_id thread_id model_id workspace_root state run_id checkpoint_id checkpoint_ns created_at updated_at last_run_status active_tool_names_json permission_mode',
        }
        if version >= 5:
            required['naiyue_sessions'] += ' thinking_level'
        if version >= 6:
            required['naiyue_sessions'] += ' title pinned sort_order'
        if version >= 7:
            required['naiyue_chat_sync'] = 'session_id thread_id checkpoint_ns agent_path checkpoint_id revision'
            required['naiyue_chat_messages'] = 'session_id thread_id checkpoint_ns message_id sequence revision body_json body_hash kind source_checkpoint_id'
        if version >= 8:
            required['naiyue_chat_sync'] += ' history_version stopped'
            required['naiyue_chat_messages'] += ' message_type parent_message_id'
        if version >= 9:
            required['naiyue_model_usage_runs'] = 'session_id run_id capture_incomplete reason dropped_events recording_closed_at'
            required['naiyue_model_invocations'] = 'invocation_id session_id run_id agent_path agent_id agent_run_id parent_run_id parent_tool_call_id model_id purpose started_at finished_at status usage_status input_tokens output_tokens cache_read_tokens cache_write_tokens error_type'
        if version >= 10:
            required['naiyue_diagnostic_runs'] = 'capture_id session_id run_id capture_state closed_at last_sequence event_bytes summary_bytes reserve_bytes summary_json'
            required['naiyue_diagnostic_events'] = 'session_id run_id sequence body_json body_bytes'
        if version >= 11:
            required['naiyue_chat_messages'] += ' reply_id'
            required['naiyue_reply_boundaries'] = 'session_id thread_id reply_id input_message_id source_checkpoint_id input_sequence'
        if version >= 12:
            required['naiyue_sessions'] += ' conversation_revision'
            required['naiyue_chat_messages'] += ' source_thread_id'
            required['naiyue_reply_boundaries'] += ' source_thread_id'
            required['naiyue_regenerations'] = 'session_id request_id reply_id revision old_thread_id new_thread_id run_id status obsolete_children'
        if version >= 13:
            required['naiyue_model_invocations'] += ' duration_ms'
        if version >= 14:
            from naiyue_test_agent.application.subagents.subagent_storage import FIELDS, LEGACY_FIELDS
            required['naiyue_subagent_instances'] = ' '.join(FIELDS if version >= 18 else LEGACY_FIELDS)
        if version >= 15:
            from naiyue_test_agent.application.memory.memory_schema import REQUIRED, check
            required.update(REQUIRED)
            check(connection, version=2 if version >= 16 else 1)
        if version >= 16:
            required['naiyue_chat_messages'] += ' occurred_at'
        if version >= 17:
            from naiyue_test_agent.application.memory.memory_overview_store import REQUIRED as OVERVIEWS
            required.update(OVERVIEWS)
        if version >= 18:
            required['naiyue_sessions'] += ' project_id execution_root_kind'
        if 'checkpoints' in tables or 'writes' in tables:
            required['checkpoints'] = 'thread_id checkpoint_ns checkpoint_id parent_checkpoint_id type checkpoint metadata'
            required['writes'] = 'thread_id checkpoint_ns checkpoint_id task_id idx channel type value'
        for table, fields in required.items():
            # Names come exclusively from the fixed schema map above.
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if table not in tables or not set(fields.split()).issubset(columns):
                raise ValueError('backup_invalid')
        if version >= 14:
            if connection.execute('''SELECT 1 FROM naiyue_subagent_instances s
                    WHERE NOT EXISTS(SELECT 1 FROM naiyue_sessions p WHERE p.session_id=s.session_id)
                    OR length(s.agent_id)!=64 OR s.agent_id GLOB '*[^0-9a-f]*'
                    OR s.child_thread_id!='subagent:'||s.agent_id
                    OR s.agent_path!='root/'||s.agent||'/'||s.agent_id
                    OR EXISTS(SELECT 1 FROM naiyue_chat_sync h WHERE h.thread_id=s.child_thread_id
                        AND (h.session_id!=s.session_id OR h.agent_path!=s.agent_path))
                    OR EXISTS(SELECT 1 FROM naiyue_sessions p WHERE p.thread_id=s.child_thread_id)
                    LIMIT 1''').fetchone():
                raise ValueError('backup_invalid')
        bindings = connection.execute('SELECT singleton,workspace_id,root_path FROM naiyue_workspace').fetchall()
        if len(bindings) > 1:
            raise ValueError('backup_invalid')
        identity = None
        if bindings:
            singleton, identity, root = bindings[0]
            if (singleton != 1 or not isinstance(identity, str) or str(UUID(identity)) != identity
                    or not isinstance(root, str) or not root or '\0' in root):
                raise ValueError('backup_invalid')
        if version >= 18:
            from .execution_storage import root_from_row
            from naiyue_test_agent.application.errors import SessionPersistenceError
            for table, child in (('naiyue_sessions', False), ('naiyue_subagent_instances', True)):
                cursor = connection.execute(f'SELECT * FROM {table}')
                names = tuple(field[0] for field in cursor.description)
                for values in cursor:
                    record = dict(zip(names, values))
                    try:
                        execution = root_from_row(record, child=child)
                    except SessionPersistenceError as error:
                        raise ValueError('backup_invalid') from error
                    if record['project_id'] != identity or identity is not None and execution is None:
                        raise ValueError('backup_invalid')
        references = [('naiyue_sessions', 'thread_id,checkpoint_ns,checkpoint_id')]
        if version >= 7:
            references.append(('naiyue_chat_sync', 'thread_id,checkpoint_ns,checkpoint_id'))
        if version >= 14:
            references.append(('naiyue_subagent_instances', 'child_thread_id,checkpoint_ns,checkpoint_id'))
        for table, columns in references:
            thread_column = 'child_thread_id' if table == 'naiyue_subagent_instances' else 'thread_id'
            if 'checkpoints' not in tables:
                if connection.execute(f'SELECT 1 FROM {table} WHERE checkpoint_id IS NOT NULL LIMIT 1').fetchone():
                    raise ValueError('backup_invalid')
                continue
            if connection.execute(f'''SELECT 1 FROM {table} s WHERE s.checkpoint_id IS NOT NULL
                    AND NOT EXISTS(SELECT 1 FROM checkpoints c WHERE c.thread_id=s.{thread_column}
                    AND c.checkpoint_ns=s.checkpoint_ns AND c.checkpoint_id=s.checkpoint_id) LIMIT 1''').fetchone():
                raise ValueError('backup_invalid')
        return {'source_schema': version, 'workspace_id': identity}
