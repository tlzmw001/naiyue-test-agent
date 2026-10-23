"""Format2 metadata and current source timestamps; all DDL is transactional."""


import json


REQUIRED = {
    'naiyue_memory_versions':'memory_id version snapshot_json',
    'naiyue_memory_suppression':'source_hash forgotten_at',
}


def upgrade(connection):
    connection.execute("ALTER TABLE naiyue_memories ADD COLUMN gaps_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(gaps_json))")
    connection.execute('ALTER TABLE naiyue_memories ADD COLUMN deadline_at REAL')
    connection.execute("ALTER TABLE naiyue_memories ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
    connection.execute('''CREATE TABLE naiyue_memory_versions(memory_id TEXT NOT NULL,version INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),PRIMARY KEY(memory_id,version))''')
    connection.execute('''CREATE TABLE naiyue_memory_suppression(source_hash TEXT PRIMARY KEY,forgotten_at REAL NOT NULL)''')
    rows=connection.execute("SELECT * FROM naiyue_memories WHERE status!='forgotten'")
    fields=[column[0] for column in rows.description]
    snapshots=(dict(zip(fields,row)) for row in rows)
    connection.executemany('INSERT INTO naiyue_memory_versions VALUES(?,?,?)',
        ((row['memory_id'],row['version'],json.dumps(row,ensure_ascii=False)) for row in snapshots))
    connection.execute('UPDATE naiyue_memory_info SET format_version=2')


def initialize_project(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0]>=16:return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('ALTER TABLE naiyue_chat_messages ADD COLUMN occurred_at REAL')
        upgrade(connection)
        connection.execute('UPDATE naiyue_store_info SET version=16')
