"""Memory format and transactional FTS projection shared by the two stores."""


REQUIRED = {
    'naiyue_memory_info': 'singleton format_version',
    'naiyue_memories': 'memory_id version scope workspace_id kind status title body aliases_json tags_json machine module target subject basis created_at updated_at evidence_at last_confirmed_at ttl_days expires_at',
    'naiyue_memory_sources': 'source_id memory_id memory_version source_workspace_id session_id thread_id message_id revision conversation_revision reply_id tool_call_id role occurred_at quote start_offset end_offset',
    'naiyue_memory_search': 'title aliases body memory_id memory_version source_digest',
}


def create(connection):
    # Individual statements stay within the caller's BEGIN IMMEDIATE transaction.
    statements = [
        'CREATE TABLE naiyue_memory_info(singleton INTEGER PRIMARY KEY CHECK(singleton=1),format_version INTEGER NOT NULL)',
        'INSERT INTO naiyue_memory_info VALUES(1,1)',
        '''CREATE TABLE naiyue_memories(memory_id TEXT PRIMARY KEY,version INTEGER NOT NULL CHECK(version>0),
            scope TEXT NOT NULL CHECK(scope IN ('workspace','personal')),workspace_id TEXT,
            kind TEXT NOT NULL CHECK(kind IN ('user','rule','decision','context','procedure','experience','reference')),
            status TEXT NOT NULL CHECK(status IN ('active','candidate','inactive','forgotten')),
            title TEXT NOT NULL CHECK(length(CAST(title AS BLOB))<=480),
            body TEXT NOT NULL CHECK(length(CAST(body AS BLOB))<=3200),
            aliases_json TEXT NOT NULL CHECK(json_valid(aliases_json)),tags_json TEXT NOT NULL CHECK(json_valid(tags_json)),
            machine TEXT,module TEXT,target TEXT,subject TEXT,
            basis TEXT NOT NULL CHECK(basis IN ('user_declaration','tool_observation','inference')),
            created_at REAL NOT NULL,updated_at REAL NOT NULL,evidence_at REAL NOT NULL,last_confirmed_at REAL,
            ttl_days INTEGER,expires_at REAL,
            CHECK((scope='workspace' AND workspace_id IS NOT NULL) OR (scope='personal' AND workspace_id IS NULL)))''',
        'CREATE INDEX naiyue_memory_listing ON naiyue_memories(scope,workspace_id,status,updated_at,memory_id)',
        '''CREATE TABLE naiyue_memory_sources(source_id INTEGER PRIMARY KEY,memory_id TEXT NOT NULL,
            memory_version INTEGER NOT NULL,source_workspace_id TEXT NOT NULL,session_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,message_id TEXT NOT NULL,revision INTEGER NOT NULL,conversation_revision INTEGER NOT NULL,
            reply_id TEXT,tool_call_id TEXT,role TEXT NOT NULL,occurred_at REAL NOT NULL,
            quote TEXT NOT NULL,start_offset INTEGER NOT NULL,end_offset INTEGER NOT NULL)''',
        'CREATE INDEX naiyue_memory_source_lookup ON naiyue_memory_sources(memory_id,memory_version,source_id)',
        '''CREATE VIRTUAL TABLE naiyue_memory_search USING fts5(title,aliases,body,memory_id UNINDEXED,
            memory_version UNINDEXED,source_digest UNINDEXED,tokenize='unicode61')''',
    ]
    for statement in statements:
        connection.execute(statement)
    insert = '''INSERT INTO naiyue_memory_search(rowid,title,aliases,body,memory_id,memory_version,source_digest)
        SELECT NEW.rowid,memory_terms(NEW.title),memory_terms(memory_aliases(NEW.aliases_json)),memory_terms(NEW.body),
        NEW.memory_id,NEW.version,memory_digest(NEW.title,NEW.aliases_json,NEW.body) WHERE NEW.status!='forgotten';'''
    connection.execute('CREATE TRIGGER naiyue_memory_insert AFTER INSERT ON naiyue_memories BEGIN ' + insert + ' END')
    connection.execute('''CREATE TRIGGER naiyue_memory_update AFTER UPDATE ON naiyue_memories BEGIN
        DELETE FROM naiyue_memory_search WHERE rowid=OLD.rowid;''' + insert + ' END')
    connection.execute('''CREATE TRIGGER naiyue_memory_delete AFTER DELETE ON naiyue_memories BEGIN
        DELETE FROM naiyue_memory_search WHERE rowid=OLD.rowid;
        DELETE FROM naiyue_memory_sources WHERE memory_id=OLD.memory_id; END''')


def check(connection, *, version=2):
    from .memory_lifecycle_schema import REQUIRED as lifecycle
    required = REQUIRED | lifecycle if version == 2 else REQUIRED
    for table, fields in required.items():
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if not set(fields.split()) <= columns:
            raise ValueError('memory_invalid_format')
    if version == 2:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(naiyue_memories)')}
        if not {'gaps_json','reason','deadline_at'} <= columns:raise ValueError('memory_invalid_format')
    info = connection.execute('SELECT singleton,format_version FROM naiyue_memory_info').fetchall()
    if [tuple(row) for row in info] != [(1, version)]:
        raise ValueError('memory_invalid_format')
    triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    if not {'naiyue_memory_insert', 'naiyue_memory_update', 'naiyue_memory_delete'} <= triggers:
        raise ValueError('memory_invalid_format')


def initialize_project(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 15:
        return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        create(connection)
        connection.execute('UPDATE naiyue_store_info SET version=15')
