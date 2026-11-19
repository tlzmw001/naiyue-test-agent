"""Single-owner, bounded Wiki retention; caller owns the write transaction."""
import time
import json


def outcome(reason,**values):
    return dict(generation_id=None,deleted_memories=0,deleted_versions=0,deleted_sources=0,
        deleted_links=0,deleted_pages=0,skipped_changed=0,more_work=False,reason=reason,duration_ms=0.,**values)


def prune(c,*,now,grace_days,batch_size,missing_personal=(),scan_state=None):
    scan_state={} if scan_state is None else scan_state
    start=time.monotonic();cutoff=now-grace_days*86400
    table=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='naiyue_memory_info'").fetchone()
    if table is None:return outcome('disabled')
    info=c.execute('SELECT * FROM naiyue_memory_info WHERE singleton=1').fetchone()
    if info is None or info['format_version']!=5:return outcome('disabled')
    result=outcome('no_candidates');result['generation_id']=info['generation_id']
    # Select and recheck under the same write transaction: confirmation cannot
    # interleave between candidate selection and deletion.
    expired=c.execute("""SELECT memory_id,version,page_id,expires_at FROM naiyue_memories
        WHERE expires_at<=? AND status IN ('active','candidate','inactive') ORDER BY expires_at,memory_id LIMIT ?""",
        (cutoff,batch_size)).fetchall()
    versions=c.execute('''SELECT v.memory_id,v.version FROM naiyue_memory_versions v
        JOIN naiyue_memories m ON m.memory_id=v.memory_id
        WHERE v.superseded_at<=? AND v.version!=m.version AND m.status!='forgotten'
        ORDER BY v.superseded_at,v.memory_id,v.version LIMIT ?''',(cutoff,batch_size)).fetchall()
    selected={(r['memory_id'],r['version']) for r in versions}
    for row in expired:
        if len(selected)>=batch_size:break
        selected.update((row['memory_id'],r[0]) for r in c.execute('''SELECT version FROM naiyue_memory_versions
            WHERE memory_id=? AND version!=? ORDER BY version LIMIT ?''',
            (row['memory_id'],row['version'],batch_size-len(selected))))
    for mid,version in selected:
        result['deleted_sources']+=c.execute('DELETE FROM naiyue_memory_sources WHERE memory_id=? AND memory_version=?',(mid,version)).rowcount
        result['deleted_versions']+=c.execute('DELETE FROM naiyue_memory_versions WHERE memory_id=? AND version=?',(mid,version)).rowcount
    changed_pages=set()
    for row in expired:
        if c.execute('SELECT 1 FROM naiyue_memory_versions WHERE memory_id=? AND version!=? LIMIT 1',
                (row['memory_id'],row['version'])).fetchone():continue
        # Current snapshots count against the same bounded version work budget.
        if result['deleted_versions']>=batch_size:continue
        deleted=c.execute("""DELETE FROM naiyue_memories WHERE memory_id=? AND version=? AND expires_at=?
            AND expires_at<=? AND status IN ('active','candidate','inactive')""",
            (row['memory_id'],row['version'],row['expires_at'],cutoff)).rowcount
        if not deleted:result['skipped_changed']+=1;continue
        mid=row['memory_id'];changed_pages.add(row['page_id']);result['deleted_memories']+=1
        result['deleted_sources']+=c.execute('DELETE FROM naiyue_memory_sources WHERE memory_id=? AND memory_version=?',(mid,row['version'])).rowcount
        result['deleted_versions']+=c.execute('DELETE FROM naiyue_memory_versions WHERE memory_id=? AND version=?',(mid,row['version'])).rowcount
    for pid in changed_pages:
        c.execute('UPDATE naiyue_memory_pages SET version=version+1,updated_at=? WHERE page_id=?',(now,pid))
    # Each invocation removes at most one bounded link batch, then one page batch.
    link_rows=c.execute('SELECT * FROM naiyue_memory_page_links WHERE (from_page_id,to_scope,to_page_id)>(?,?,?) ORDER BY from_page_id,to_scope,to_page_id LIMIT ?',
        (*scan_state.get('links',('','','')),batch_size+1)).fetchall()
    link_more=len(link_rows)>batch_size
    link_rows=link_rows[:batch_size]
    scan_state['links']=tuple(link_rows[-1][k] for k in ('from_page_id','to_scope','to_page_id')) if link_more else ('','','')
    links=[]
    for link in link_rows:
        source=c.execute('SELECT 1 FROM naiyue_memories WHERE page_id=? LIMIT 1',(link['from_page_id'],)).fetchone()
        target=c.execute('SELECT 1 FROM naiyue_memories WHERE page_id=? LIMIT 1',(link['to_page_id'],)).fetchone() if link['to_scope']==info['scope'] else True
        if not source or not target or link['to_scope']=='personal' and link['to_page_id'] in missing_personal:links.append(link)
    for link in links:
        result['deleted_links']+=c.execute('DELETE FROM naiyue_memory_page_links WHERE from_page_id=? AND to_scope=? AND to_page_id=?',tuple(link[k] for k in ('from_page_id','to_scope','to_page_id'))).rowcount
        c.execute('UPDATE naiyue_memory_pages SET version=version+1,updated_at=? WHERE page_id=?',(now,link['from_page_id']))
    page_rows=c.execute('SELECT page_id FROM naiyue_memory_pages WHERE page_id>? ORDER BY page_id LIMIT ?',
        (scan_state.get('pages',''),batch_size+1)).fetchall()
    page_more=len(page_rows)>batch_size
    scan_state['pages']=page_rows[batch_size-1][0] if page_more else ''
    priority=list(changed_pages)+[l['to_page_id'] for l in links if l['to_scope']==info['scope']]
    candidates=list(dict.fromkeys(priority+[r[0] for r in page_rows[:batch_size]]))
    page_more |= len(candidates)>batch_size
    candidates=candidates[:batch_size]
    pages=[]
    for pid in candidates:
        if c.execute('SELECT 1 FROM naiyue_memories WHERE page_id=? LIMIT 1',(pid,)).fetchone():continue
        if c.execute('SELECT 1 FROM naiyue_memory_page_links WHERE from_page_id=? LIMIT 1',(pid,)).fetchone():continue
        if c.execute('SELECT 1 FROM naiyue_memory_page_links WHERE to_scope=? AND to_page_id=? LIMIT 1',(info['scope'],pid)).fetchone():continue
        pages.append((pid,))
    for row in pages:result['deleted_pages']+=c.execute('DELETE FROM naiyue_memory_pages WHERE page_id=?',(row[0],)).rowcount
    if changed_pages or links or pages:c.execute('UPDATE naiyue_memory_info SET catalog_revision=catalog_revision+1')
    pending=c.execute("""SELECT 1 FROM naiyue_memories WHERE expires_at<=? AND status IN ('active','candidate','inactive') LIMIT 1""",(cutoff,)).fetchone()
    old=c.execute('''SELECT 1 FROM naiyue_memory_versions v JOIN naiyue_memories m ON m.memory_id=v.memory_id
        WHERE v.superseded_at<=? AND v.version!=m.version AND m.status!='forgotten' LIMIT 1''',(cutoff,)).fetchone()
    remaining_links=c.execute("SELECT 1 FROM naiyue_memory_page_links WHERE to_scope='personal' AND to_page_id IN (SELECT value FROM json_each(?)) LIMIT 1",(json.dumps(list(missing_personal)),)).fetchone()
    result['more_work']=bool(pending or old or page_more or link_more or remaining_links)
    if any(result[k] for k in ('deleted_memories','deleted_versions','deleted_links','deleted_pages')):result['reason']='pruned'
    result['duration_ms']=(time.monotonic()-start)*1000
    return result
