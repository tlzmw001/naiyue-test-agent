"""Single-store CAS commits, deterministic lifecycle gates and exact-source suppression."""

import json
from uuid import uuid4

from . import memory_sources as evidence
from .memory_results import check_metadata
from naiyue_test_agent.contracts.memory import MemoryReadError, CONDITIONS


def _public(row):
    result={k:row[k] for k in ('memory_id','version','scope','kind','status','title','body','evidence_at',
        'last_confirmed_at','ttl_days','expires_at','reason')}
    result['gaps']=json.loads(row['gaps_json'])
    return result


def commit(writer, archive, workspace_id, caller, request, settings, now):
    action, scope = request['action'],request['scope']
    old = None
    if action != 'record':
        found = writer.current(request['memory_id'],scope,workspace_id)
        if found is None:raise MemoryReadError('memory_not_found')
        old=dict(found)
        if old['version']!=request['expected_version'] or old['status']=='forgotten':
            raise MemoryReadError('memory_conflict')
    sources=evidence.resolve(archive,workspace_id,caller,request['sources'],now)
    if action in ('disable','forget'):
        if not any(s['user_origin'] for s in sources):raise MemoryReadError('memory_user_source_required')
        evidence.sensitive(request['reason'])
        return _retire(writer,old,sources,action,request['reason'],now)
    for source in sources:
        if writer.suppressed(evidence.identity(source)):
            raise MemoryReadError('memory_source_suppressed')
    p=request['proposal'];evidence.sensitive(p)
    if old and p['kind']!=old['kind']:raise MemoryReadError('memory_kind_changed')
    previous = writer.sources(old['memory_id'],old['version']) if old else []
    known={evidence.identity(s) for s in previous}
    fresh=[s for s in sources if evidence.identity(s) not in known and (old is None or s['occurred_at']>old['evidence_at'])]
    at=max(s['occurred_at'] for s in fresh) if fresh else old['evidence_at'] if old else max(s['occurred_at'] for s in sources)
    gaps=list(p['gaps'])
    if p['intent']=='activate':
        if p['basis']=='user_declaration' and not any(s['user_origin'] for s in sources):gaps.append('user_source_required')
        if p['basis']=='tool_observation' and not (p['target'] and any(s['tool_success'] for s in sources)):gaps.append('verified_tool_target_required')
        if p['basis']=='inference':gaps.append('direct_evidence_required')
        if old and (old['status']!='active' or old['expires_at'] is not None and old['expires_at']<=now) and not fresh:
            gaps.append('new_evidence_required')
    else:
        if not gaps:gaps.append('activation_not_requested')
    # Exact current known conflicts only; semantic conflict detection stays with the proposing model.
    matches=writer.matches(scope,workspace_id,p,now,old['memory_id'] if old else '')
    if not old and any(r['body']==p['body'] for r in matches):raise MemoryReadError('memory_duplicate')
    if any(r['body']!=p['body'] for r in matches):gaps.append('known_conflict')
    status='active' if p['intent']=='activate' and not gaps else 'candidate'
    ttl=(settings.active_ttl_days if status=='active' else settings.candidate_ttl_days).get(p['kind'])
    expiry=at+ttl*86400 if ttl else None
    deadline=p['deadline'] if p['deadline'] is not None else old['deadline_at'] if old else None
    if p['deadline'] is not None:
        if not any(s['user_origin'] for s in sources):raise MemoryReadError('memory_deadline_user_source_required')
    if deadline is not None:expiry=min(expiry,deadline) if expiry is not None else deadline
    if old and not fresh:
        if old['status']==status:
            ttl=old['ttl_days'];expiry=old['expires_at']
        elif old['expires_at'] is not None:
            expiry=min(expiry,old['expires_at']) if expiry is not None else old['expires_at']
        if p['deadline'] is not None:expiry=min(expiry,p['deadline']) if expiry is not None else p['deadline']
    row=dict(memory_id=old['memory_id'] if old else uuid4().hex,version=old['version']+1 if old else 1,
        scope=scope,workspace_id=workspace_id if scope=='workspace' else None,kind=p['kind'],status=status,
        title=p['title'],body=p['body'],aliases_json=json.dumps(p['aliases'],ensure_ascii=False),
        tags_json=json.dumps(p['tags'],ensure_ascii=False),**{k:p[k] for k in CONDITIONS},basis=p['basis'],
        created_at=old['created_at'] if old else now,updated_at=now,evidence_at=at,
        last_confirmed_at=at if status=='active' else old['last_confirmed_at'] if old else None,
        ttl_days=ttl,expires_at=expiry,deadline_at=deadline,gaps_json=json.dumps(list(dict.fromkeys(gaps)),ensure_ascii=False),reason=p['reason'])
    merged={evidence.identity(s):s for s in [*previous,*sources]}
    # Evidence history is bounded per version; old versions retain their original references until forgotten.
    check_metadata(row,settings)
    writer.save(row,sorted(merged.values(),key=lambda s:s['occurred_at'],reverse=True)[:8])
    return dict(status='saved' if status=='active' else 'still_candidate',item=_public(row))


def _retire(writer,old,sources,action,reason,now):
    row=dict(old)
    row.update(version=old['version']+1,updated_at=now,status='forgotten' if action=='forget' else 'inactive',reason=reason)
    previous=writer.sources(old['memory_id'],old['version'])
    if action=='forget':
        all_sources=writer.sources(old['memory_id'])
        hashes=[evidence.identity(s) for s in all_sources]
        row.update(title='',body='',aliases_json='[]',tags_json='[]',gaps_json='[]',reason='',
            machine=None,module=None,target=None,subject=None)
        writer.forget(row,hashes,now)
    else:
        writer.save(row,list({evidence.identity(s):s for s in [*previous,*sources]}.values())[-8:])
    return dict(status='forgotten' if action=='forget' else 'saved',item=_public(row))
