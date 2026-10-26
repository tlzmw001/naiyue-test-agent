"""Memory data operations within the caller's existing write transaction."""

import json


FIELDS = ('memory_id','version','scope','workspace_id','kind','status','title','body','aliases_json','tags_json',
    'machine','module','target','subject','basis','created_at','updated_at','evidence_at','last_confirmed_at',
    'ttl_days','expires_at','gaps_json','reason','deadline_at')
SOURCE_FIELDS = ('source_workspace_id','session_id','thread_id','message_id','revision','conversation_revision',
    'reply_id','tool_call_id','role','occurred_at','quote','start_offset','end_offset')


class MemoryWriter:
    def __init__(self, connection):
        self._connection = connection

    def current(self, memory_id, scope, workspace_id):
        row = self._connection.execute('''SELECT * FROM naiyue_memories WHERE memory_id=? AND scope=?
            AND (workspace_id=? OR scope='personal')''', (memory_id, scope, workspace_id)).fetchone()
        return dict(row) if row is not None else None

    def sources(self, memory_id, version=None):
        if version is None:
            rows = self._connection.execute('SELECT * FROM naiyue_memory_sources WHERE memory_id=?', (memory_id,))
        else:
            rows = self._connection.execute('SELECT * FROM naiyue_memory_sources WHERE memory_id=? AND memory_version=?',
                (memory_id, version))
        return [dict(row) for row in rows]

    def suppressed(self, source_hash):
        return self._connection.execute('SELECT 1 FROM naiyue_memory_suppression WHERE source_hash=?',
            (source_hash,)).fetchone() is not None

    def matches(self, scope, workspace_id, proposal, now, excluded_id):
        rows = self._connection.execute('''SELECT memory_id,body FROM naiyue_memories WHERE scope=? AND workspace_id IS ?
            AND kind=? AND subject=? AND machine IS ? AND module IS ? AND target IS ? AND status='active'
            AND (expires_at IS NULL OR expires_at>?) AND memory_id!=?''',
            (scope, workspace_id if scope=='workspace' else None, proposal['kind'], proposal['subject'],
             proposal['machine'], proposal['module'], proposal['target'], now, excluded_id))
        return [dict(row) for row in rows]

    def save(self, row, sources):
        columns = ','.join(FIELDS)
        self._connection.execute(f'''INSERT INTO naiyue_memories({columns}) VALUES({','.join('?' for _ in FIELDS)})
            ON CONFLICT(memory_id) DO UPDATE SET {','.join(k+'=excluded.'+k for k in FIELDS[1:])}''', tuple(row[k] for k in FIELDS))
        self._connection.execute('INSERT INTO naiyue_memory_versions VALUES(?,?,?)',
            (row['memory_id'], row['version'], json.dumps(row, ensure_ascii=False)))
        self._connection.executemany(f'''INSERT INTO naiyue_memory_sources(memory_id,memory_version,{','.join(SOURCE_FIELDS)})
            VALUES({','.join('?' for _ in range(len(SOURCE_FIELDS)+2))})''',
            [(row['memory_id'], row['version'], *(s[k] for k in SOURCE_FIELDS)) for s in sources])

    def forget(self, row, source_hashes, now):
        self._connection.executemany('INSERT OR IGNORE INTO naiyue_memory_suppression VALUES(?,?)',
            [(value, now) for value in source_hashes])
        self._connection.execute('DELETE FROM naiyue_memory_sources WHERE memory_id=?', (row['memory_id'],))
        self._connection.execute('DELETE FROM naiyue_memory_versions WHERE memory_id=?', (row['memory_id'],))
        self._connection.execute('''UPDATE naiyue_memories SET version=?,status='forgotten',title='',body='',aliases_json='[]',
            tags_json='[]',gaps_json='[]',reason='',machine=NULL,module=NULL,target=NULL,subject=NULL,updated_at=?
            WHERE memory_id=?''', (row['version'], now, row['memory_id']))
