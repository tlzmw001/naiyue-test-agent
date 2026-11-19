"""Run-bound Wiki discovery and signed stable-key continuation; no model calls."""
import base64
import binascii
import hashlib
import hmac
import json
from naiyue_test_agent.contracts.memory import MemoryReadError, CONDITIONS
from .memory_wiki_render import envelope,fit


def _encode(key,value):
    data=json.dumps(value,separators=(',',':'),ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(hmac.digest(key,data,'sha256')+data).decode()


def _decode(key,token,binding):
    if token is None:return None
    try:
        raw=base64.b64decode(token,altchars=b'-_',validate=True)
        if not hmac.compare_digest(raw[:32],hmac.digest(key,raw[32:],'sha256')):raise ValueError()
        value=json.loads(raw[32:])
        if value['binding']!=binding:raise ValueError()
        return value
    except (ValueError,TypeError,KeyError,binascii.Error,UnicodeError):
        raise MemoryReadError('memory_cursor_stale') from None


def query(access,namespace,caller,request,now,*,include_candidates=False,max_tokens=None,allows_link=None,stores=None):
    action=request['action']
    if action not in ('list_topics','list_pages','read_page'):raise MemoryReadError('memory_wiki_action_unavailable')
    selector={k:v for k,v in request.items() if k!='cursor'}
    binding=hashlib.sha256(json.dumps([namespace,caller,selector,include_candidates],sort_keys=True).encode()).hexdigest()
    cursor=_decode(access._wiki_cursor_key,request.get('cursor'),binding)
    if cursor and cursor['expiry'] is not None and now>=cursor['expiry']:raise MemoryReadError('memory_cursor_stale')
    if stores is None:
        stores=access._stores(request['scope'])
    views=[];position=cursor['position'] if cursor else None
    for scope,repo in stores:
        after=None
        if position:
            if scope<position[0]:continue
            if scope==position[0]:
                after=position[1:3]
                # A partial entry must be returned again, not skipped by the seek.
                if action=='read_page' and position[3]>0:
                    after=cursor['previous']
        view=repo.wiki_view(scope,namespace if scope=='workspace' else None,request,now=now,
            after=after,include_candidates=include_candidates)
        views.append((scope,repo,view))
    # Always bind all visible stores, even when continuation has passed one of them.
    states={}
    for scope,repo in stores:
        current=next((v for s,_,v in views if s==scope),None)
        if current is None:
            current=repo.wiki_view(scope,namespace if scope=='workspace' else None,dict(action='list_topics'),now=now)
        states[scope]=[current['generation_id'],current['catalog_revision']]
    if cursor and cursor['states']!=states:raise MemoryReadError('memory_cursor_stale')
    expiries=[v['expires_at'] for _,_,v in views if v['expires_at'] is not None]
    expiry=min(expiries) if expiries else None
    if cursor and cursor['expiry'] is not None:expiry=min(expiry,cursor['expiry']) if expiry is not None else cursor['expiry']
    generations={s:v[0] for s,v in states.items()}
    def token(pos,previous=None):
        return _encode(access._wiki_cursor_key,dict(binding=binding,states=states,expiry=expiry,position=pos,previous=previous))
    def bounded(value):
        return fit(value,max_tokens=max_tokens or access.settings.wiki_read_tokens,max_bytes=min(16384,access.settings.max_output_bytes))
    if action=='list_topics':
        topics=['collaboration','working_agreements','stable_knowledge']
        result=envelope(generations,dict(topics=[dict(scope=s,topic=t,action='list_pages') for s,_ in stores for t in topics]),[])
        result=bounded(result)
        if result is None:raise MemoryReadError('memory_budget_too_small')
        return result
    if action=='list_pages':
        rows=sorted(
            [(s,r) for s,_,v in views for r in v['rows']],key=lambda x:(x[0],x[1]['title'],x[1]['page_id']))
        # Do not advance into a later store until a capped earlier store is exhausted.
        capped=[s for s,_,v in views if len(v['rows'])==50]
        if capped:rows=[(s,r) for s,r in rows if s<=min(capped)]
        items=[];texts=[];accepted=None
        uncertain=any(len(v['rows'])==50 for _,_,v in views)
        for index,(scope,row) in enumerate(rows):
            pos=[scope,row['title'],row['page_id'],0]
            more=index+1<len(rows) or uncertain
            item={k:row[k] for k in ('page_id','scope','version')}
            text=json.dumps(dict(row,action='read_page'),ensure_ascii=False)
            value=bounded(envelope(generations,'{"pages": ['+','.join(texts+[text])+']}',items+[item],cursor=token(pos) if more else None))
            if value is None:break
            items.append(item);texts.append(text);accepted=value
        if accepted is not None:return accepted
        if rows:raise MemoryReadError('memory_budget_too_small')
        value=bounded(envelope(generations,dict(pages=[]),[]))
        if value is None:raise MemoryReadError('memory_budget_too_small')
        return value
    scope,repo,view=views[0]
    page=view['page']
    if request.get('version') is not None and page['version']!=request['version']:raise MemoryReadError('memory_version_changed')
    links=[]
    for target_scope in ('workspace','personal'):
        ids=[l['to_page_id'] for l in view['links'] if l['to_scope']==target_scope]
        if target_scope=='workspace' and scope!='workspace':continue
        if allows_link is not None:
            ids = [page_id for page_id in ids if allows_link(dict(
                action='read_page', scope=target_scope, page_id=page_id, version=None, cursor=None))]
        if not ids:continue
        targets=dict(access._stores(target_scope)).get(target_scope)
        if targets is None:continue
        legal=targets.wiki_targets(target_scope,namespace if target_scope=='workspace' else None,ids,now=now)
        links.extend(dict(r,scope=target_scope) for r in legal)
    header={k:page[k] for k in ('page_id','scope','topic','title','description','version')}
    prefix=json.dumps(dict(page=header),ensure_ascii=False)[:-1]+', "entries": ['
    suffix='], "links": '+json.dumps(links,ensure_ascii=False)+'}'
    rows=view['rows'];entries=[];items=[];accepted=None
    previous=cursor['previous'] if cursor else None
    for index,row in enumerate(rows):
        start=position[3] if position and row['memory_id']==position[2] else 0
        if start>len(row['body']):raise MemoryReadError('memory_cursor_stale')
        base={k:row[k] for k in ('memory_id','version','title','status','basis','evidence_at','expires_at')}
        base['conditions']={k:row[k] for k in CONDITIONS}
        if row['status']=='candidate' or json.loads(row['gaps_json']):
            base.update(gaps=json.loads(row['gaps_json']),reason=row['reason'])
        if row['deadline_at'] is not None:base['deadline_at']=row['deadline_at']
        source_rows=view['sources'].get(row['memory_id'],[])
        base['sources']=[dict(s,original_state='not_checked' if s['source_workspace_id']==namespace else 'other_workspace')
            for s in source_rows[:access.settings.source_limit]]
        base['sources_truncated']=len(source_rows)>access.settings.source_limit
        def attempt(end):
            done=end==len(row['body']);pos=[scope,row['expires_at'],row['memory_id'],0 if done else end]
            more=not done or index+1<len(rows) or len(rows)==50
            body=dict(base,body=row['body'][start:end],offset=start,entry_complete=done)
            item=dict(memory_id=row['memory_id'],version=row['version'],offset=start,next_offset=end,entry_complete=done)
            rendered=json.dumps(body,ensure_ascii=False)
            value=envelope(generations,prefix+','.join(entries+[rendered])+suffix,items+[item],
                cursor=token(pos,[row['expires_at'],row['memory_id']] if done else previous) if more else None,
                scope=scope,page_id=page['page_id'],version=page['version'])
            return bounded(value),rendered,item
        value,body,item=attempt(len(row['body']))
        if value is None:
            if entries:break
            lo,hi=start+1,len(row['body'])-1;best=None
            while lo<=hi:
                end=(lo+hi)//2;trial=attempt(end)
                if trial[0] is not None:best=trial;lo=end+1
                else:hi=end-1
            if best is None:raise MemoryReadError('memory_budget_too_small')
            # Prefer a nearby sentence/line boundary, preserving every character.
            end=best[2]['next_offset']
            boundaries=[row['body'].rfind(mark,start+(end-start)//2,end)+1 for mark in ('\n','。','；',' ') ]
            boundary=max(boundaries)
            if boundary>start:
                natural=attempt(boundary)
                if natural[0] is not None:return natural[0]
            return best[0]
        entries.append(body);items.append(item);accepted=value
        previous=[row['expires_at'],row['memory_id']]
    if accepted is not None:return accepted
    value=bounded(envelope(generations,dict(page=header,entries=[],links=links),[],scope=scope,page_id=page['page_id'],version=page['version']))
    if value is None:raise MemoryReadError('memory_budget_too_small')
    return value
