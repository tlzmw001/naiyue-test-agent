"""Format4: preserve evidence clocks and history while narrowing current kinds.

The caller owns the transaction and backup. No archive reads or model calls.
"""
import json
import time
from .writes import MemoryWriter

KINDS = dict.fromkeys(('user','rule','decision'),'convention') | dict.fromkeys(
    ('context','procedure','experience','reference'),'context')
OLD_CHECK = "kind IN ('user','rule','decision','context','procedure','experience','reference')"
NEW_CHECK = "kind IN ('convention','context')"


def upgrade(connection):
    version=connection.execute('SELECT format_version FROM naiyue_memory_info').fetchone()[0]
    if version==4:return
    if version!=3:raise ValueError('memory_invalid_format')
    now=time.time()
    rows=[dict(r) for r in connection.execute('SELECT * FROM naiyue_memories')]
    table=connection.execute("SELECT sql FROM sqlite_master WHERE name='naiyue_memories'").fetchone()[0]
    if OLD_CHECK not in table:raise ValueError('memory_invalid_format')
    triggers=[r[0] for r in connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='naiyue_memories'")]
    indexes=[r[0] for r in connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='naiyue_memories' AND sql IS NOT NULL")]
    writer=MemoryWriter(connection)
    sources={r['memory_id']:writer.sources(r['memory_id'],r['version']) for r in rows}
    for name in ('insert','update','delete'):connection.execute('DROP TRIGGER naiyue_memory_'+name)
    connection.execute('DROP TABLE naiyue_memories')
    connection.execute(table.replace(OLD_CHECK,NEW_CHECK))
    connection.execute('DELETE FROM naiyue_memory_search')
    for sql in indexes+triggers:connection.execute(sql)
    groups={}
    for row in rows:
        row['kind']=KINDS[row['kind']]
        if row['status']=='forgotten':continue
        ttl=90 if row['kind']=='convention' and row['status']!='candidate' else 30
        row['ttl_days']=ttl
        row['expires_at']=min(v for v in (row['evidence_at']+ttl*86400,row['expires_at'],row['deadline_at']) if v is not None)
        if row['status']=='active' and row['expires_at']>now:
            key=tuple(row[k] for k in ('scope','workspace_id','subject','machine','module','target'))
            groups.setdefault(key,[]).append(row)
    for group in groups.values():
        if len({r['body'] for r in group})>1:
            for row in group:
                row['status']='candidate';row['ttl_days']=30
                row['expires_at']=min(row['expires_at'],row['evidence_at']+30*86400)
                row['gaps_json']=json.dumps(list(dict.fromkeys([*json.loads(row['gaps_json']),'legacy_kind_merge_conflict'])))
    for row in rows:
        if row['status']=='forgotten':
            columns=list(row)
            connection.execute('INSERT INTO naiyue_memories('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row.values()))
        else:
            row['version']+=1
            writer.save(row,sources[row['memory_id']])
    connection.execute('UPDATE naiyue_memory_info SET format_version=4')


def check(connection):
    sql=connection.execute("SELECT sql FROM sqlite_master WHERE name='naiyue_memories'").fetchone()[0]
    if NEW_CHECK not in sql or OLD_CHECK in sql:raise ValueError('memory_invalid_format')


def initialize_project(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0]>=25:
        check(connection);return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        upgrade(connection)
        connection.execute('UPDATE naiyue_store_info SET version=25')
