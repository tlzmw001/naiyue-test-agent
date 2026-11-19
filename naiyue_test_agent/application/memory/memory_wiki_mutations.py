"""Compile one direct Wiki change with verified provenance into one atomic group."""
import json
from uuid import uuid4
from naiyue_test_agent.contracts.memory import MemoryReadError,CONDITIONS
from . import memory_sources as evidence
from .memory_wiki_render import envelope,fit


def _fail(reason):
    raise MemoryReadError(reason)


def _merge(old,new):
    merged={evidence.span_identity(s):dict(s) for s in old}
    for source in new:
        key=evidence.span_identity(source)
        merged[key]=dict(source,supports_scope=source['supports_scope'] or merged.get(key,{}).get('supports_scope',False))
    if len(merged)>8:_fail('memory_source_limit')
    return list(merged.values())


def commit(writer,archive,workspace_id,caller,request,settings,now):
    scope,action=request['scope'],request['action']
    old=writer.current(request['memory_id'],scope,workspace_id) if action!='record' else None
    if action!='record':
        if old is None:_fail('memory_not_found')
        if old['version']!=request['expected_version'] or old['status']=='forgotten':_fail('memory_conflict')
    sources=evidence.resolve_wiki(archive,workspace_id,caller,request['sources'],request['scope_source_indexes'],now)
    if action not in ('disable','forget') and any(writer.suppressed(evidence.identity(s)) for s in sources):_fail('memory_source_suppressed')
    previous=writer.sources(old['memory_id'],old['version']) if old else []
    if action=='record':
        dest=request['destination']
        if 'new_page' in dest:
            evidence.sensitive(dest['new_page'])
            page=writer.new_page(**dest['new_page'])
        else:
            page=writer.page(dest['page_id'])
            page=dict(page,expected_version=dest['expected_page_version'])
    else:
        page=writer.page(old['page_id'])
        page=dict(page,expected_version=page['version'])
    mode=request['change']['mode'] if action=='update' else action
    if mode in ('disable','forget'):
        if not any(s['user_origin'] for s in sources):_fail('memory_user_source_required')
        evidence.sensitive(request['reason'])
        row=dict(old,version=old['version']+1,updated_at=now,status='forgotten' if mode=='forget' else 'inactive',reason=request['reason'])
        # Retirement is authorized by the new source without adding it to positive evidence.
        retained=previous
    elif mode=='supplement':
        row=dict(old,version=old['version']+1,updated_at=now)
        retained=_merge(previous,sources)
    else:
        p=request['proposal'] if mode=='record' else request['change'].get('proposal')
        if mode=='confirm':
            if old['status']!='active':_fail('memory_activation_required')
            p={k:old[k] for k in ('title','body','basis',*CONDITIONS)}
            p.update(aliases=json.loads(old['aliases_json']),tags=json.loads(old['tags_json']),
                intent='activate',gaps=json.loads(old['gaps_json']),reason=old['reason'],deadline=old['deadline_at'])
        evidence.sensitive(p)
        valid=[s for s in sources if s['user_origin']] if p['basis']=='user_declaration' else [
            s for s in sources if s['tool_success'] and p.get('target')] if p['basis']=='tool_observation' else list(sources)
        if not valid:_fail('memory_direct_source_required')
        if scope=='personal' and not any(s['user_origin'] for s in sources):_fail('memory_user_source_required')
        fresh=[s for s in valid if old is None or s['occurred_at']>old['evidence_at']]
        if old and not fresh:_fail('memory_new_evidence_required')
        at=max(s['occurred_at'] for s in fresh)
        ttl=settings.default_ttl_days
        deadline=p['deadline']
        if old and old['deadline_at'] is not None:deadline=min(deadline,old['deadline_at']) if deadline is not None else old['deadline_at']
        if mode!='confirm' and p['deadline'] is not None and not any(s['user_origin'] for s in sources):_fail('memory_deadline_user_source_required')
        expiry=min(at+ttl*86400,deadline) if deadline is not None else at+ttl*86400
        if expiry<=now:_fail('memory_evidence_expired')
        matches=writer.matches(scope,workspace_id,p,now,old['memory_id'] if old else '')
        if not old and any(r['body']==p['body'] for r in matches):_fail('memory_duplicate')
        gaps=list(p['gaps'])
        if any(r['body']!=p['body'] for r in matches):gaps.append('known_conflict')
        if p['basis']=='inference':gaps.append('direct_evidence_required')
        if p['intent']!='activate' and not gaps:gaps.append('activation_not_requested')
        status='active' if p['intent']=='activate' and not gaps else 'candidate'
        if mode=='confirm':
            row=dict(old,version=old['version']+1,updated_at=now,evidence_at=at,last_confirmed_at=at,ttl_days=ttl,expires_at=expiry,
                status=status,gaps_json=json.dumps(list(dict.fromkeys(gaps)),ensure_ascii=False))
        else:
            row=dict(memory_id=old['memory_id'] if old else uuid4().hex,version=old['version']+1 if old else 1,
                scope=scope,workspace_id=workspace_id if scope=='workspace' else None,page_id=page['page_id'],status=status,
                title=p['title'],body=p['body'],aliases_json=json.dumps(p['aliases'],ensure_ascii=False),
                tags_json=json.dumps(p['tags'],ensure_ascii=False),**{k:p[k] for k in CONDITIONS},basis=p['basis'],
                created_at=old['created_at'] if old else now,updated_at=now,evidence_at=at,last_confirmed_at=at if status=='active' else None,
                ttl_days=ttl,expires_at=expiry,deadline_at=deadline,gaps_json=json.dumps(list(dict.fromkeys(gaps)),ensure_ascii=False),reason=p['reason'])
        retained=_merge([s for s in previous if s['supports_scope']],sources)
    if scope=='personal' and not any(s['supports_scope'] for s in retained):_fail('memory_scope_evidence_required')
    generation=writer.generation_id
    receipt={k:row[k] for k in ('memory_id','page_id','version','scope','status','ttl_days','evidence_at','expires_at')}
    result=envelope({scope:generation},{'item':receipt},[receipt])
    result.update(status='forgotten' if mode=='forget' else 'still_candidate' if row['status']=='candidate' else 'saved',item=dict(receipt))
    # The receipt points to the saved ID; do not echo/truncate the body on writes.
    result=fit(result,max_tokens=settings.wiki_read_tokens,max_bytes=min(settings.max_output_bytes,16384))
    if result is None:_fail('memory_budget_too_small')
    writer.apply(pages=[page],memories=[dict(row=row,sources=retained,expected_version=old['version'] if old else None,
        require_unexpired=mode not in ('confirm','disable','forget'))])
    if mode=='forget':
        all_sources=writer.sources(row['memory_id'])
        writer.forget(row,[evidence.identity(s) for s in [*all_sources,*sources]],now)
    return result
