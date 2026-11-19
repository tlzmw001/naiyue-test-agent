"""Remove post-v10 structures when constructing a genuine older schema fixture."""


def remove_execution_schema(connection):
    remove_memory_evidence(connection)
    remove_project_local_schema(connection)
    for table, fields in (('naiyue_sessions', ('project_id', 'execution_root_kind')),
            ('naiyue_subagent_instances', ('project_id', 'execution_root', 'execution_root_kind'))):
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        for field in fields:
            if field in columns:
                connection.execute(f'ALTER TABLE {table} DROP COLUMN {field}')


def remove_project_local_schema(connection):
    remove_memory_evidence(connection)
    remove_environment_schema(connection)
    for table in ('naiyue_sessions', 'naiyue_subagent_instances'):
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        for field in ('execution_binding', 'last_execution_path'):
            if field in columns:
                connection.execute(f'ALTER TABLE {table} DROP COLUMN {field}')


def remove_environment_schema(connection):
    remove_memory_evidence(connection)
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


def remove_memory_evidence(connection):
    remove_memory_time(connection)
    columns={r[1] for r in connection.execute('PRAGMA table_info(naiyue_memory_sources)')}
    if 'verified_at' not in columns:return
    connection.execute('DROP INDEX IF EXISTS naiyue_memory_source_message')
    for column in ('verified_at','tool_success','user_origin','evidence_format'):
        connection.execute('ALTER TABLE naiyue_memory_sources DROP COLUMN '+column)
    connection.execute('UPDATE naiyue_memory_info SET format_version=2')


def remove_memory_time(connection):
    """Restore an empty historical table's actual CHECK, not just its version label."""
    if connection.execute('SELECT format_version FROM naiyue_memory_info').fetchone()[0]<4:return
    assert connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==0
    from naiyue_test_agent.storage.memory_sql.memory_time_schema import OLD_CHECK,NEW_CHECK
    sql=connection.execute("SELECT sql FROM sqlite_master WHERE name='naiyue_memories'").fetchone()[0]
    definitions=[r[0] for r in connection.execute("SELECT sql FROM sqlite_master WHERE tbl_name='naiyue_memories' AND sql IS NOT NULL AND type IN ('index','trigger')")]
    for name in ('insert','update','delete'):connection.execute('DROP TRIGGER naiyue_memory_'+name)
    connection.execute('DROP TABLE naiyue_memories')
    connection.execute(sql.replace(NEW_CHECK,OLD_CHECK))
    for statement in definitions:connection.execute(statement)
    connection.execute('UPDATE naiyue_memory_info SET format_version=3')
