"""Wiki format5 DDL; caller owns the transaction. Never used by default startup yet."""
from uuid import uuid4

FORMAT = 5
PROJECT_SCHEMA = 26
TOPICS = ('collaboration','working_agreements','stable_knowledge')


def create(c, *, scope, workspace_id, now):
    if scope not in ('workspace','personal') or (scope=='personal') != (workspace_id is None):
        raise ValueError('memory_scope_mismatch')
    c.execute('''CREATE TABLE naiyue_memory_info(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        format_version INTEGER NOT NULL CHECK(format_version=5),generation_id TEXT NOT NULL,
        catalog_revision INTEGER NOT NULL CHECK(catalog_revision>=0),started_at REAL NOT NULL,
        scope TEXT NOT NULL CHECK(scope IN ('workspace','personal')),workspace_id TEXT,
        CHECK((scope='personal' AND workspace_id IS NULL) OR (scope='workspace' AND workspace_id IS NOT NULL)))''')
    c.execute('INSERT INTO naiyue_memory_info VALUES(1,5,?,0,?,?,?)',(uuid4().hex,now,scope,workspace_id))
    c.execute('''CREATE TABLE naiyue_memory_pages(page_id TEXT PRIMARY KEY,scope TEXT NOT NULL,workspace_id TEXT,
        topic TEXT NOT NULL CHECK(topic IN ('collaboration','working_agreements','stable_knowledge')),
        title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 160 AND length(CAST(title AS BLOB))<=480),
        description TEXT NOT NULL CHECK(length(description)<=300 AND length(CAST(description AS BLOB))<=900),
        version INTEGER NOT NULL CHECK(version>0),created_at REAL NOT NULL,updated_at REAL NOT NULL)''')
    c.execute('CREATE INDEX wiki_catalog ON naiyue_memory_pages(topic,title,page_id)')
    c.execute('''CREATE TABLE naiyue_memories(memory_id TEXT PRIMARY KEY,version INTEGER NOT NULL CHECK(version>0),
        scope TEXT NOT NULL,workspace_id TEXT,page_id TEXT NOT NULL REFERENCES naiyue_memory_pages(page_id),
        status TEXT NOT NULL CHECK(status IN ('active','candidate','inactive','forgotten')),
        title TEXT NOT NULL CHECK(length(title)<=160 AND length(CAST(title AS BLOB))<=480),
        body TEXT NOT NULL CHECK(length(CAST(body AS BLOB))<=3200),
        aliases_json TEXT NOT NULL CHECK(json_valid(aliases_json)),tags_json TEXT NOT NULL CHECK(json_valid(tags_json)),
        machine TEXT,module TEXT,target TEXT,subject TEXT,basis TEXT NOT NULL CHECK(basis IN ('user_declaration','tool_observation','inference')),
        created_at REAL NOT NULL,updated_at REAL NOT NULL,evidence_at REAL NOT NULL,last_confirmed_at REAL,
        ttl_days INTEGER NOT NULL CHECK(ttl_days BETWEEN 1 AND 36500),expires_at REAL NOT NULL,
        gaps_json TEXT NOT NULL CHECK(json_valid(gaps_json)),reason TEXT NOT NULL,deadline_at REAL)''')
    c.execute('CREATE INDEX wiki_members ON naiyue_memories(page_id,status,expires_at,memory_id)')
    c.execute('CREATE INDEX wiki_expiry ON naiyue_memories(expires_at,memory_id)')
    c.execute('''CREATE TABLE naiyue_memory_versions(memory_id TEXT NOT NULL,version INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),superseded_at REAL,
        PRIMARY KEY(memory_id,version))''')
    c.execute('CREATE INDEX wiki_superseded ON naiyue_memory_versions(superseded_at,memory_id,version)')
    c.execute('''CREATE TABLE naiyue_memory_sources(source_id INTEGER PRIMARY KEY,memory_id TEXT NOT NULL,
        memory_version INTEGER NOT NULL,source_workspace_id TEXT NOT NULL,session_id TEXT NOT NULL,thread_id TEXT NOT NULL,
        message_id TEXT NOT NULL,revision INTEGER NOT NULL,conversation_revision INTEGER NOT NULL,reply_id TEXT,tool_call_id TEXT,
        role TEXT NOT NULL,occurred_at REAL NOT NULL,quote TEXT NOT NULL CHECK(length(quote)<=300),
        start_offset INTEGER NOT NULL,end_offset INTEGER NOT NULL,evidence_format INTEGER,user_origin INTEGER,
        tool_success INTEGER,verified_at REAL,supports_scope INTEGER NOT NULL CHECK(supports_scope IN (0,1)),
        CHECK(supports_scope=0 OR (role='human' AND user_origin=1 AND evidence_format=1 AND verified_at IS NOT NULL)))''')
    c.execute('CREATE INDEX wiki_sources ON naiyue_memory_sources(memory_id,memory_version,source_id)')
    c.execute('''CREATE TABLE naiyue_memory_suppression(source_hash TEXT PRIMARY KEY,forgotten_at REAL NOT NULL)''')
    c.execute('''CREATE TABLE naiyue_memory_page_links(from_page_id TEXT NOT NULL REFERENCES naiyue_memory_pages(page_id),
        to_scope TEXT NOT NULL CHECK(to_scope IN ('workspace','personal')),to_page_id TEXT NOT NULL,
        description TEXT NOT NULL CHECK(length(description)<=160),PRIMARY KEY(from_page_id,to_scope,to_page_id))''')
    c.execute('CREATE INDEX wiki_inbound ON naiyue_memory_page_links(to_scope,to_page_id,from_page_id)')
    # Explicit triggers also protect scopes on connections with foreign_keys disabled.
    for event in ('INSERT','UPDATE'):
        c.execute(f'''CREATE TRIGGER wiki_page_owner_{event.lower()} BEFORE {event} ON naiyue_memory_pages BEGIN
            SELECT RAISE(ABORT,'memory_scope_mismatch') WHERE NOT EXISTS(
                SELECT 1 FROM naiyue_memory_info WHERE scope=NEW.scope AND workspace_id IS NEW.workspace_id); END''')
        c.execute(f'''CREATE TRIGGER wiki_member_owner_{event.lower()} BEFORE {event} ON naiyue_memories BEGIN
            SELECT RAISE(ABORT,'memory_scope_mismatch') WHERE NOT EXISTS(
                SELECT 1 FROM naiyue_memory_pages WHERE page_id=NEW.page_id AND scope=NEW.scope AND workspace_id IS NEW.workspace_id); END''')
    c.execute('''CREATE VIRTUAL TABLE naiyue_memory_search USING fts5(title,aliases,body,memory_id UNINDEXED,
        memory_version UNINDEXED,source_digest UNINDEXED,tokenize='unicode61')''')
    insert = '''INSERT INTO naiyue_memory_search(rowid,title,aliases,body,memory_id,memory_version,source_digest)
        SELECT NEW.rowid,memory_terms(NEW.title),memory_terms(memory_aliases(NEW.aliases_json)),memory_terms(NEW.body),
        NEW.memory_id,NEW.version,memory_digest(NEW.title,NEW.aliases_json,NEW.body) WHERE NEW.status!='forgotten';'''
    c.execute('CREATE TRIGGER wiki_search_insert AFTER INSERT ON naiyue_memories BEGIN '+insert+' END')
    c.execute('CREATE TRIGGER wiki_search_update AFTER UPDATE ON naiyue_memories BEGIN DELETE FROM naiyue_memory_search WHERE rowid=OLD.rowid;'+insert+' END')
    c.execute('CREATE TRIGGER wiki_search_delete AFTER DELETE ON naiyue_memories BEGIN DELETE FROM naiyue_memory_search WHERE rowid=OLD.rowid; END')


def owner(c, scope, workspace_id):
    from naiyue_test_agent.contracts.memory import MemoryReadError
    row=c.execute('SELECT * FROM naiyue_memory_info WHERE singleton=1').fetchone()
    if row is None or row['format_version']!=FORMAT or (row['scope'],row['workspace_id'])!=(scope,workspace_id):
        raise MemoryReadError('memory_scope_mismatch')
    return dict(row)


def check(c):
    from .memory_wiki_writes import FIELDS, SOURCES
    row=c.execute('SELECT * FROM naiyue_memory_info').fetchall()
    if len(row)!=1 or row[0]['format_version']!=FORMAT:raise ValueError('memory_invalid_format')
    columns={r[1] for r in c.execute('PRAGMA table_info(naiyue_memories)')}
    if 'kind' in columns or not set(FIELDS)<=columns:raise ValueError('memory_invalid_format')
    for table, fields in {
        'naiyue_memory_info':{'generation_id','catalog_revision','started_at','scope','workspace_id'},
        'naiyue_memory_pages':{'topic','version','description','scope','workspace_id'},
        'naiyue_memory_sources':set(SOURCES)|{'source_id','memory_id','memory_version'},
        'naiyue_memory_versions':{'superseded_at','snapshot_json'},
        'naiyue_memory_page_links':{'from_page_id','to_scope','to_page_id'},
        'naiyue_memory_suppression':{'source_hash','forgotten_at'},
        'naiyue_memory_search':{'title','aliases','body','memory_id','memory_version','source_digest'},
    }.items():
        if not fields<={r[1] for r in c.execute(f'PRAGMA table_info({table})')}:raise ValueError('memory_invalid_format')
    for name,fields in {
        'wiki_catalog':['topic','title','page_id'],
        'wiki_members':['page_id','status','expires_at','memory_id'],
        'wiki_expiry':['expires_at','memory_id'],
        'wiki_superseded':['superseded_at','memory_id','version'],
        'wiki_sources':['memory_id','memory_version','source_id'],
        'wiki_inbound':['to_scope','to_page_id','from_page_id'],
    }.items():
        if [r[2] for r in c.execute(f'PRAGMA index_info({name})')]!=fields:raise ValueError('memory_invalid_format')
    triggers={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    required={'wiki_page_owner_insert','wiki_page_owner_update','wiki_member_owner_insert','wiki_member_owner_update',
              'wiki_search_insert','wiki_search_update','wiki_search_delete'}
    if not required<=triggers:raise ValueError('memory_invalid_format')
    info=row[0]
    try:
        from uuid import UUID
        if UUID(info['generation_id']).hex!=info['generation_id']:raise ValueError('memory_invalid_format')
    except (ValueError,TypeError,AttributeError) as error:raise ValueError('memory_invalid_format') from error
    if c.execute('''SELECT 1 FROM naiyue_memory_pages WHERE scope!=? OR workspace_id IS NOT ? LIMIT 1''',
                 (info['scope'],info['workspace_id'])).fetchone():raise ValueError('memory_invalid_format')
    if c.execute('''SELECT 1 FROM naiyue_memories m LEFT JOIN naiyue_memory_pages p ON p.page_id=m.page_id
        WHERE p.page_id IS NULL OR p.scope!=m.scope OR p.workspace_id IS NOT m.workspace_id LIMIT 1''').fetchone():
        raise ValueError('memory_invalid_format')
    if c.execute('PRAGMA foreign_key_check').fetchone() is not None:raise ValueError('memory_invalid_format')
