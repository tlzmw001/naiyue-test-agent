"""Format3 evidence attributes; caller owns transaction and pre-upgrade backup."""
FIELDS='evidence_format user_origin tool_success verified_at'
VALID="""((evidence_format IS NULL AND user_origin IS NULL AND tool_success IS NULL AND verified_at IS NULL)
 OR (evidence_format IS NOT NULL AND evidence_format=1 AND user_origin IS NOT NULL AND user_origin IN (0,1)
 AND tool_success IS NOT NULL AND tool_success IN (0,1) AND verified_at IS NOT NULL))"""


def upgrade(connection):
    for column in ('evidence_format INTEGER','user_origin INTEGER','tool_success INTEGER'):
        connection.execute('ALTER TABLE naiyue_memory_sources ADD COLUMN '+column)
    connection.execute('ALTER TABLE naiyue_memory_sources ADD COLUMN verified_at REAL CHECK('+VALID+')')
    connection.execute('''CREATE INDEX naiyue_memory_source_message ON naiyue_memory_sources
        (memory_id,source_workspace_id,session_id,thread_id,message_id,revision,conversation_revision)''')
    connection.execute('UPDATE naiyue_memory_info SET format_version=3')


def check(connection):
    columns={r[1] for r in connection.execute('PRAGMA table_info(naiyue_memory_sources)')}
    if not set(FIELDS.split())<=columns:raise ValueError('memory_invalid_format')
    indexes={r[1] for r in connection.execute('PRAGMA index_list(naiyue_memory_sources)')}
    if 'naiyue_memory_source_message' not in indexes:raise ValueError('memory_invalid_format')
    indexed=[r[2] for r in connection.execute('PRAGMA index_info(naiyue_memory_source_message)')]
    if indexed!=['memory_id','source_workspace_id','session_id','thread_id','message_id','revision','conversation_revision']:
        raise ValueError('memory_invalid_format')
    sql=connection.execute("SELECT sql FROM sqlite_master WHERE name='naiyue_memory_sources'").fetchone()[0]
    if ''.join(VALID.split()) not in ''.join(sql.split()):raise ValueError('memory_invalid_format')
    if connection.execute('SELECT 1 FROM naiyue_memory_sources WHERE NOT '+VALID+' LIMIT 1').fetchone():
        raise ValueError('memory_invalid_format')


def initialize_project(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0]>=24:
        check(connection)
        return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        upgrade(connection)
        connection.execute('UPDATE naiyue_store_info SET version=24')
