"""Remove post-v10 structures when constructing a genuine older schema fixture."""


def remove_execution_schema(connection):
    remove_project_local_schema(connection)
    for table, fields in (('naiyue_sessions', ('project_id', 'execution_root_kind')),
            ('naiyue_subagent_instances', ('project_id', 'execution_root', 'execution_root_kind'))):
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        for field in fields:
            if field in columns:
                connection.execute(f'ALTER TABLE {table} DROP COLUMN {field}')


def remove_project_local_schema(connection):
    remove_environment_schema(connection)
    for table in ('naiyue_sessions', 'naiyue_subagent_instances'):
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        for field in ('execution_binding', 'last_execution_path'):
            if field in columns:
                connection.execute(f'ALTER TABLE {table} DROP COLUMN {field}')


def remove_environment_schema(connection):
    connection.execute('DROP TABLE IF EXISTS naiyue_project_identity')
    for table,column in (('naiyue_sessions','execution_setup_json'),('naiyue_subagent_instances','environment_binding_id')):
        columns={row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        if column in columns:connection.execute(f'ALTER TABLE {table} DROP COLUMN {column}')


def remove_memory_overviews(connection):
    remove_execution_schema(connection)
    for table in ('naiyue_memory_overviews','naiyue_memory_progress'):
        connection.execute(f'DROP TABLE IF EXISTS {table}')


def remove_memory_lifecycle(connection):
    remove_memory_overviews(connection)
    for table in ('naiyue_memory_versions','naiyue_memory_suppression'):
        connection.execute(f'DROP TABLE IF EXISTS {table}')
    for table,column in (('naiyue_chat_messages','occurred_at'),('naiyue_memories','gaps_json'),('naiyue_memories','reason'),('naiyue_memories','deadline_at')):
        if column in {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}:
            connection.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='naiyue_memory_info'").fetchone():
        connection.execute('UPDATE naiyue_memory_info SET format_version=1')


def remove_memory_schema(connection):
    remove_memory_lifecycle(connection)
    for table in ('naiyue_memory_search', 'naiyue_memories', 'naiyue_memory_sources', 'naiyue_memory_info'):
        connection.execute(f'DROP TABLE IF EXISTS {table}')


def remove_reply_schema(connection):
    remove_memory_schema(connection)
    connection.execute('DROP TABLE IF EXISTS naiyue_subagent_instances')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(naiyue_model_invocations)')}
    if 'duration_ms' in columns:
        connection.execute('ALTER TABLE naiyue_model_invocations DROP COLUMN duration_ms')
    connection.executescript('''
        DROP TABLE IF EXISTS naiyue_regenerations;
        DROP TABLE IF EXISTS naiyue_reply_boundaries;
        ALTER TABLE naiyue_sessions DROP COLUMN conversation_revision;
        ALTER TABLE naiyue_chat_messages DROP COLUMN reply_id;
        ALTER TABLE naiyue_chat_messages DROP COLUMN source_thread_id;
    ''')
