"""Real versioned SQLite fixtures shared only by Spec123 behavior tests."""

import json
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver

from naiyue_test_agent.application.session_storage import SessionStorage


def source_store(tmp_path, *, version=14, bound=True, checkpoints=True):
    root = tmp_path / 'workspace'
    (root / '.naiyue').mkdir(parents=True)
    identity = str(uuid4())
    (root / '.naiyue/workspace.json').write_text(json.dumps({'version': 1, 'workspace_id': identity}))
    database = tmp_path / 'custom.sqlite'
    storage = SessionStorage(database)
    if bound:
        storage.bind_workspace(identity, root)
    storage.create('S', {'thread_id': 'T', 'model_id': 'M', 'workspace_root': root,
        'active_tool_names': (), 'permission_mode': 'Default'})
    storage.mark_running('S', 'R')
    connection = storage.connection
    if checkpoints:
        SqliteSaver(connection).setup()
        connection.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)',
                           ('T', '', 'C', 'pruned-parent', 'json', b'opaque-checkpoint', b'opaque-metadata'))
        connection.execute('INSERT INTO writes VALUES(?,?,?,?,?,?,?,?)',
                           ('T', '', 'C', 'task', 0, 'pending', 'json', b'opaque-value'))
        connection.execute("UPDATE naiyue_sessions SET checkpoint_id='C'")
    if version < 14:
        connection.execute('DROP TABLE naiyue_subagent_instances')
    if version < 13:
        connection.execute('ALTER TABLE naiyue_model_invocations DROP COLUMN duration_ms')
    if version < 12:
        connection.execute('DROP TABLE naiyue_regenerations')
        connection.execute('ALTER TABLE naiyue_sessions DROP COLUMN conversation_revision')
        connection.execute('ALTER TABLE naiyue_chat_messages DROP COLUMN source_thread_id')
        connection.execute('ALTER TABLE naiyue_reply_boundaries DROP COLUMN source_thread_id')
    if version < 11:
        connection.execute('DROP TABLE naiyue_reply_boundaries')
        connection.execute('ALTER TABLE naiyue_chat_messages DROP COLUMN reply_id')
    if version < 10:
        connection.execute('DROP TABLE naiyue_diagnostic_events')
        connection.execute('DROP TABLE naiyue_diagnostic_runs')
    if version < 9:
        connection.execute('DROP TABLE naiyue_model_invocations')
        connection.execute('DROP TABLE naiyue_model_usage_runs')
    if version < 8:
        for name in ('history_version', 'stopped'):
            connection.execute(f'ALTER TABLE naiyue_chat_sync DROP COLUMN {name}')
        for name in ('message_type', 'parent_message_id'):
            connection.execute(f'ALTER TABLE naiyue_chat_messages DROP COLUMN {name}')
    if version < 7:
        connection.execute('DROP TABLE naiyue_chat_sync')
        connection.execute('DROP TABLE naiyue_chat_messages')
    if version < 6:
        for name in ('title', 'pinned', 'sort_order'):
            connection.execute(f'ALTER TABLE naiyue_sessions DROP COLUMN {name}')
    if version < 5:
        connection.execute('ALTER TABLE naiyue_sessions DROP COLUMN thinking_level')
    connection.execute('UPDATE naiyue_store_info SET version=?', (version,))
    connection.commit()
    return storage, root, identity


def artifact(database, backup_id):
    return Path(database).with_name(database.name + '.backups') / backup_id


def closed_store(tmp_path, *, version=14):
    storage, root, identity = source_store(tmp_path, version=version)
    database = storage.path
    storage.close()
    return database, root, identity
