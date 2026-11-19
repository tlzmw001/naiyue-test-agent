"""Bounded Wiki projections. Owner checks precede metadata/body reads."""
from naiyue_test_agent.contracts.memory import MemoryReadError
from .memory_wiki_schema import owner, TOPICS


def _limit(limit):
    if type(limit) is not int or not 1<=limit<=50:raise MemoryReadError('memory_limit_invalid')


def page(c, scope, workspace_id, page_id):
    owner(c,scope,workspace_id)
    row=c.execute('SELECT * FROM naiyue_memory_pages WHERE page_id=?',(page_id,)).fetchone()
    if row is None:raise MemoryReadError('memory_not_found')
    return dict(row)


def pages(c,scope,workspace_id,topic,*,now,after=None,limit=50,include_candidates=False):
    owner(c,scope,workspace_id);_limit(limit)
    if topic not in TOPICS:raise MemoryReadError('memory_topic_invalid')
    statuses="('active','candidate')" if include_candidates else "('active')"
    seek=' AND (p.title,p.page_id)>(?,?)' if after else ''
    sql=f'''SELECT p.page_id,p.scope,p.topic,p.title,p.description,p.version FROM naiyue_memory_pages p
        WHERE p.topic=? {seek} AND EXISTS(SELECT 1 FROM naiyue_memories m
        WHERE m.page_id=p.page_id AND m.status IN {statuses} AND m.expires_at>?)
        ORDER BY p.title,p.page_id LIMIT ?'''
    return [dict(r) for r in c.execute(sql,(topic,*(after or ()),now,limit))]


def members(c,scope,workspace_id,page_id,*,now,after=None,limit=50,include_candidates=False):
    page(c,scope,workspace_id,page_id);_limit(limit)
    statuses="('active','candidate')" if include_candidates else "('active')"
    seek=' AND (expires_at,memory_id)>(?,?)' if after else ''
    return [dict(r) for r in c.execute(f'''SELECT * FROM naiyue_memories WHERE page_id=?
        AND status IN {statuses} AND expires_at>? {seek} ORDER BY expires_at,memory_id LIMIT ?''',
        (page_id,now,*(after or ()),limit))]


def sources(c, identities):
    info=c.execute('SELECT format_version FROM naiyue_memory_info WHERE singleton=1').fetchone()
    if info is None or info[0]!=5:raise MemoryReadError('memory_scope_mismatch')
    if not identities:return []
    if len(identities)>50:raise MemoryReadError('memory_limit_invalid')
    result=[dict(r) for r in c.execute('SELECT * FROM naiyue_memory_sources WHERE (memory_id,memory_version) IN ('+
        ','.join('(?,?)' for _ in identities)+') ORDER BY memory_id,memory_version,source_id',
        tuple(value for pair in identities for value in pair))]
    for row in result:
        for key in ('user_origin','tool_success','supports_scope'):
            if row[key] is not None:row[key]=bool(row[key])
    return result


def links(c,scope,workspace_id,page_id,*,now):
    page(c,scope,workspace_id,page_id)
    # Cross-store personal targets require application filtering again; never return their titles here.
    return [dict(r) for r in c.execute('''SELECT l.* FROM naiyue_memory_page_links l
        WHERE l.from_page_id=? AND (l.to_scope!=? OR EXISTS(SELECT 1 FROM naiyue_memories m
        WHERE m.page_id=l.to_page_id AND m.status='active' AND m.expires_at>?))
        ORDER BY l.to_scope,l.to_page_id LIMIT 8''',(page_id,scope,now))]


def view(c,scope,workspace_id,request,*,now,after=None,include_candidates=False):
    """One consistent, bounded projection and its invalidation metadata."""
    info=owner(c,scope,workspace_id)
    statuses="('active','candidate')" if include_candidates else "('active')"
    if request.get('status')=='candidate':statuses="('candidate')"
    elif request.get('status')=='all':statuses="('active','candidate')"
    expiry=c.execute(f'''SELECT expires_at FROM naiyue_memories INDEXED BY wiki_expiry
        WHERE expires_at>? AND status IN {statuses} ORDER BY expires_at,memory_id LIMIT 1''',(now,)).fetchone()
    result=dict(generation_id=info['generation_id'],catalog_revision=info['catalog_revision'],
        expires_at=expiry[0] if expiry else None,rows=[],page=None,links=[])
    if request['action']=='list_pages':
        result['rows']=pages(c,scope,workspace_id,request['topic'],now=now,after=after,include_candidates=include_candidates)
    elif request['action']=='read_page':
        result['page']=page(c,scope,workspace_id,request['page_id'])
        result['rows']=members(c,scope,workspace_id,request['page_id'],now=now,after=after,include_candidates=include_candidates)
        result['links']=links(c,scope,workspace_id,request['page_id'],now=now)
        ids=[(r['memory_id'],r['version']) for r in result['rows']]
        result['sources']={}
        if ids:
            # Metadata only, one bounded batch; archive/quote reads remain explicit.
            metadata=c.execute("SELECT memory_id,memory_version,source_workspace_id,session_id,thread_id,message_id,"
                "revision,conversation_revision,role,occurred_at,supports_scope FROM naiyue_memory_sources WHERE "
                "(memory_id,memory_version) IN ("+','.join('(?,?)' for _ in ids)+") ORDER BY source_id",
                tuple(v for pair in ids for v in pair))
            for row in metadata:
                result['sources'].setdefault(row['memory_id'],[]).append(dict(row))
    return result


def targets(c,scope,workspace_id,ids,*,now):
    owner(c,scope,workspace_id)
    if len(ids)>8:raise MemoryReadError('memory_link_capacity')
    if not ids:return []
    return [dict(r) for r in c.execute('''SELECT page_id,title,description,version FROM naiyue_memory_pages p
        WHERE page_id IN ('''+','.join('?' for _ in ids)+''') AND EXISTS(SELECT 1 FROM naiyue_memories m
        WHERE m.page_id=p.page_id AND m.status='active' AND m.expires_at>?)''',(*ids,now))]
