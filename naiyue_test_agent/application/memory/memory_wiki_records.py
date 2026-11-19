"""Existing lexical selection and ID reads projected through the Wiki output budget."""
import hashlib
import json
from naiyue_test_agent.contracts.memory import MemoryReadError,CONDITIONS
from .memory_wiki_reads import _encode,_decode
from .memory_wiki_render import envelope,fit


def query(access,namespace,caller,request,now,*,stores):
    action=request['action'];store_map=dict(stores)
    selector={k:v for k,v in request.items() if k!='cursor'}
    binding=hashlib.sha256(json.dumps([namespace,caller,selector],sort_keys=True).encode()).hexdigest()
    cursor=_decode(access._wiki_cursor_key,request.get('cursor'),binding)
    snapshots={s:r.wiki_view(s,namespace if s=='workspace' else None,dict(action='list_topics',status=request['status']),now=now) for s,r in stores}
    states={s:[v['generation_id'],v['catalog_revision']] for s,v in snapshots.items()}
    if cursor and (cursor['states']!=states or cursor['expiry'] is not None and cursor['expiry']<=now):
        raise MemoryReadError('memory_cursor_stale')
    expiries=[v['expires_at'] for v in snapshots.values() if v['expires_at'] is not None]
    expiry=min(expiries) if expiries else None
    generations={s:v[0] for s,v in states.items()}
    count=request.get('limit') or access.settings.default_limit
    if count>access.settings.max_limit:raise MemoryReadError('memory_invalid_limit')
    before=cursor['position'] if cursor else None
    truncated=False
    if action=='search':
        variants=tuple(dict.fromkeys((request['query'],*request['expansions'])))
        if any(len(q)>access.settings.max_query_chars for q in variants):raise MemoryReadError('memory_query_too_complex')
        rows,truncated=access._search(stores,namespace,request,now,variants,count)
    elif action=='list':
        rows=[row for s,r in stores for row in r.listing(s,namespace,request,now,before,count+1)]
        rows.sort(key=lambda r:(-r['updated_at'],r['scope'],r['memory_id']))
        truncated=len(rows)>count;rows=rows[:count]
    else:
        scope,repo=stores[0]
        row=repo.current(scope,namespace,request,now,request['memory_id'])
        if row is None:raise MemoryReadError('memory_not_found')
        if request['version'] is not None and request['version']!=row['version']:raise MemoryReadError('memory_version_changed')
        rows=[row]
    history_state=None
    if action=='read' and request['include_history']:
        history_state=hashlib.sha256(json.dumps(stores[0][1].versions(rows[0]['memory_id']),sort_keys=True).encode()).hexdigest()
        if cursor and cursor.get('history_state')!=history_state:raise MemoryReadError('memory_cursor_stale')
    # Bounded batch source IO, one query per selected store, no cross-project archive opens.
    sources={}
    for scope,repo in stores:
        ids=[(r['memory_id'],r['version']) for r in rows if r['scope']==scope]
        for source in repo.wiki_sources(ids):sources.setdefault((scope,source['memory_id']),[]).append(source)
    def checked(result):
        for scope,repo in stores:
            current=repo.wiki_view(scope,namespace if scope=='workspace' else None,dict(action='list_topics',status=request['status']),now=now)
            if [current['generation_id'],current['catalog_revision']]!=states[scope]:
                raise MemoryReadError('memory_cursor_stale')
        if history_state is not None and hashlib.sha256(json.dumps(stores[0][1].versions(rows[0]['memory_id']),sort_keys=True).encode()).hexdigest()!=history_state:
            raise MemoryReadError('memory_cursor_stale')
        return result
    accepted=None;entries=[];items=[]
    for i,row in enumerate(rows):
        scope=row['scope'];repo=store_map[scope]
        start=(cursor['offset'] if cursor else request['offset']) if action=='read' else 0
        if start>len(row['body']):raise MemoryReadError('memory_invalid_offset')
        base={k:row[k] for k in ('memory_id','page_id','version','scope','status','title','basis','evidence_at','expires_at','ttl_days')}
        base['conditions']={k:row[k] for k in CONDITIONS}
        base.update({k:row[k] for k in ('created_at','updated_at','last_confirmed_at')})
        base.update(aliases=json.loads(row['aliases_json']),tags=json.loads(row['tags_json']),
            needs_verification=any(row[k] is not None and request[k] is None for k in CONDITIONS))
        for key in ('duplicate_ids','literal_match','match_coverage'):
            if key in row:base[key]=row[key]
        if action=='read':
            base.update(gaps=json.loads(row['gaps_json']),reason=row['reason'],deadline_at=row['deadline_at'])
        source_rows=sources.get((scope,row['memory_id']),[])
        visible=source_rows[:access.settings.source_limit]
        if action=='read':
            base['sources']=[access.storage.conversation.source_reference(namespace,s,detail=True) for s in visible]
            if request['include_history']:
                versions=repo.versions(row['memory_id']);base['history']=versions[:5];base['history_truncated']=len(versions)>5
        else:
            base['sources']=[{k:s[k] for k in ('source_workspace_id','session_id','message_id','role','occurred_at')} for s in visible]
        base['sources_truncated']=len(source_rows)>len(visible)
        def attempt(end):
            done=end==len(row['body']);item=dict(memory_id=row['memory_id'],page_id=row['page_id'],scope=scope,version=row['version'],
                entry_complete=done,offset=start,next_offset=end)
            body=dict(base,body=row['body'][start:end],offset=start,entry_complete=done)
            more=not done if action=='read' else i+1<len(rows) or truncated
            token=None
            if more and action!='search':
                position=[row['updated_at'],scope,row['memory_id']] if action=='list' else None
                token=_encode(access._wiki_cursor_key,dict(binding=binding,states=states,expiry=expiry,position=position,offset=end,history_state=history_state))
            value=envelope(generations,'{"entries": ['+','.join(entries+[json.dumps(body,ensure_ascii=False)])+']}',items+[item],cursor=token,
                truncated=truncated if action=='search' else False)
            if action=='read':value.update(next_offset=end)
            if action=='search' and (more or not done):value.update(truncated=True,complete=False)
            return fit(value,max_tokens=access.settings.wiki_read_tokens,max_bytes=min(access.settings.max_output_bytes,16384)),body,item
        end=len(row['body']) if action=='read' else min(len(row['body']),access.settings.preview_chars)
        value,body,item=attempt(end)
        if value is None and entries:break
        while value is None and (base.get('history') or base['sources']):
            if base.get('history'):
                base['history'].pop();base['history_truncated']=True
            else:
                base['sources'].pop();base['sources_truncated']=True
            value,body,item=attempt(end)
        if value is None:
            lo,hi=start+1,end-1;best=None
            while lo<=hi:
                mid=(lo+hi)//2;trial=attempt(mid)
                if trial[0] is not None:best=trial;lo=mid+1
                else:hi=mid-1
            if best is None:raise MemoryReadError('memory_budget_too_small')
            value,body,item=best
        entries.append(json.dumps(body,ensure_ascii=False));items.append(item);accepted=value
        if action=='read':return checked(accepted)
    if accepted is not None:return checked(accepted)
    result=fit(envelope(generations,dict(entries=[]),[],truncated=truncated),max_tokens=access.settings.wiki_read_tokens,
        max_bytes=min(access.settings.max_output_bytes,16384))
    if result is None:raise MemoryReadError('memory_budget_too_small')
    return checked(result)
