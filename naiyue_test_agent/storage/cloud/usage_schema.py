"""Optional Coordinator usage tables, separate from required journal/projection."""
USAGE_SCHEMA=(
    '''CREATE TABLE history_usage(invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,thread_id TEXT NOT NULL,
        source_json TEXT NOT NULL,record_json TEXT NOT NULL,started INTEGER NOT NULL,finished INTEGER NOT NULL)''',
    'CREATE INDEX history_usage_run ON history_usage(run_id)',
    '''CREATE TABLE history_usage_runs(run_id TEXT PRIMARY KEY,capture_incomplete INTEGER NOT NULL,
        dropped_events INTEGER NOT NULL,closed INTEGER NOT NULL,reason TEXT)''')
