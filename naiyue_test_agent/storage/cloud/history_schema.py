"""Coordinator-only v2 projection tables. The immutable v1 event journal stays intact."""
PROJECTION_SCHEMA=(
    '''CREATE TABLE history_messages(thread_id TEXT NOT NULL,message_id TEXT NOT NULL,revision INTEGER NOT NULL,
        seq INTEGER NOT NULL UNIQUE,position INTEGER NOT NULL,record_json TEXT NOT NULL,body_json TEXT NOT NULL,
        preview_json TEXT NOT NULL,owner_input INTEGER NOT NULL,run_id TEXT NOT NULL,message_type TEXT NOT NULL,
        PRIMARY KEY(thread_id,message_id,revision))''',
    'CREATE INDEX history_message_position ON history_messages(thread_id,position,seq)',
    '''CREATE TABLE history_threads(thread_id TEXT NOT NULL,seq INTEGER NOT NULL,record_json TEXT NOT NULL,
        PRIMARY KEY(thread_id,seq))''',
    '''CREATE TABLE history_replies(thread_id TEXT NOT NULL,reply_id TEXT NOT NULL,seq INTEGER NOT NULL,
        record_json TEXT NOT NULL,PRIMARY KEY(thread_id,reply_id,seq))''',
    '''CREATE TABLE history_transfers(transfer_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,header_json TEXT NOT NULL,
        next_part INTEGER NOT NULL,body_bytes INTEGER NOT NULL)''',
    '''CREATE TABLE history_parts(transfer_id TEXT NOT NULL REFERENCES history_transfers(transfer_id) ON DELETE CASCADE,
        part INTEGER NOT NULL,text TEXT NOT NULL,PRIMARY KEY(transfer_id,part))''',
    'CREATE TABLE history_owners(command_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,message TEXT NOT NULL)',
    '''CREATE TABLE history_legacy(thread_id TEXT NOT NULL,message_id TEXT NOT NULL,seq INTEGER NOT NULL,
        run_id TEXT NOT NULL,record_json TEXT NOT NULL,body_json TEXT NOT NULL,PRIMARY KEY(thread_id,message_id))''')
