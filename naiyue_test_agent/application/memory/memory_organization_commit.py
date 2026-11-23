"""Compile connected Wiki decisions under one source/target transaction."""
import json
import time
from uuid import uuid4
from naiyue_test_agent.contracts.memory import MemoryReadError,CONDITIONS
from naiyue_test_agent.tools.memory_wiki_actions import compile_wiki_request
from naiyue_test_agent.tools.memory_writes import MemorySource
from .memory_wiki_protocol import Finish
from .memory_wiki_mutations import commit,_merge
from . import memory_sources as evidence


def fail(code='memory_organization_invalid'):
    raise MemoryReadError(code)


class Staged:
    def __init__(self,writer):
        self.writer=writer;self.pages={};self.memories={};self.links=[]

    def __getattr__(self,name):return getattr(self.writer,name)

    def matches(self,scope,workspace_id,proposal,now,excluded_id):
        rows=[r for r in self.writer.matches(scope,workspace_id,proposal,now,excluded_id) if r['memory_id'] not in self.memories]
        for item in self.memories.values():
            row=item['row']
            if (row['memory_id']!=excluded_id and row['scope']==scope and row['status']=='active'
                    and row['expires_at']>now and all(row[k]==proposal[k] for k in CONDITIONS)):
                rows.append(dict(memory_id=row['memory_id'],body=row['body']))
        return rows

    def apply(self,*,pages=(),memories=(),**kwargs):
        for page in pages:self.pages[page['page_id']]=page
        for item in memories:
            mid=item['row']['memory_id']
            if mid in self.memories:fail('memory_duplicate_target')
            self.memories[mid]=item


def _compile(stage,archive,namespace,caller,ledger,proposals,raw,settings,now,allowed):
    finished=Finish.model_validate(raw)
    pages={};consumed=[];oldused=set();dirty=set()
    def page(ref):
        if ref in pages:return pages[ref]
        metadata=ledger.require_page(ref)
        result=dict(stage.writer.page(ref),expected_version=metadata['version'])
        pages[ref]=result;return result
    for change in finished.page_changes:
        if not allowed(dict(action='record' if change.ref.startswith('N') else 'update',scope=ledger.scope)):fail('memory_permission_denied')
        evidence.sensitive(change.model_dump())
        dirty.add(change.ref)
        if change.ref.startswith('N'):
            if change.ref in pages or change.topic not in ledger.directories:fail('memory_read_incomplete')
            pages[change.ref]=stage.new_page(change.topic,change.title,change.description)
        else:
            old=page(change.ref)
            if change.topic!=old['topic'] and change.topic not in ledger.directories:fail('memory_read_incomplete')
            pages[change.ref]=dict(old,topic=change.topic,title=change.title,description=change.description)
    def old(ref):
        if ref not in ledger.old:fail('memory_not_discovered')
        snapshot=ledger.old[ref];page(snapshot['page_id'])
        row=stage.current(snapshot['memory_id'],ledger.scope,namespace if ledger.scope=='workspace' else None)
        if row is None or row['version']!=snapshot['version'] or row['expires_at']<=now:fail('memory_conflict')
        return row
    def authorize(action,row=None):
        selector=dict(action=action,scope=ledger.scope)
        if row:selector.update(memory_id=row['memory_id'],expected_version=row['version'])
        if not allowed(selector):fail('memory_permission_denied')
    for d in finished.decisions:
        if d.relation == 'add' and d.destination is None:
            fail('memory_destination_required')
        if len(set(d.proposal_ids))!=len(d.proposal_ids) or not set(d.proposal_ids)<=proposals.keys():fail()
        consumed.extend(d.proposal_ids)
        if len(set(d.old_refs))!=len(d.old_refs):fail()
        rows=[old(ref) for ref in d.old_refs]
        if d.target_old_ref is not None and d.target_old_ref not in d.old_refs:fail()
        target=old(d.target_old_ref) if d.target_old_ref else None
        if d.relation=='unsupported':continue
        if d.relation=='duplicate':
            if target is None:fail()
            continue
        if oldused.intersection(d.old_refs):fail('memory_duplicate_target')
        oldused.update(d.old_refs)
        dirty.update(r['page_id'] for r in rows)
        chosen=[proposals[pid] for pid in d.proposal_ids]
        if d.relation in ('add','unresolved_conflict') and d.proposal is None and len(chosen)==1 and not d.evidence_ids:
            d=d.model_copy(update={'evidence_ids':list(chosen[0]['evidence_ids'])})
        source_map={eid:(s,eid in p['scope_evidence_ids']) for p in chosen for eid,s in zip(p['evidence_ids'],p['sources'])}
        if len(set(d.evidence_ids))!=len(d.evidence_ids) or not set(d.evidence_ids)<=source_map.keys():fail()
        selected_units={u['evidence_id']:u for p in chosen for u in p['evidence']}
        if d.evidence_ids and not any(selected_units[e]['origin']=='new' for e in d.evidence_ids):fail('memory_new_evidence_required')
        refs=[MemorySource.model_validate(source_map[e][0]).model_dump(mode='json') for e in d.evidence_ids]
        indexes=[i for i,e in enumerate(d.evidence_ids) if source_map[e][1]]
        if d.relation in ('move','split'):
            if target is None or len(rows)!=1 or chosen or refs or d.destination is None:fail()
            authorize('update',target);dest=page(d.destination)
            row=dict(target,page_id=dest['page_id'],version=target['version']+1,updated_at=now)
            stage.apply(memories=[dict(row=row,sources=stage.sources(target['memory_id'],target['version']),expected_version=target['version'])])
            continue
        if d.relation=='merge':
            if target is None or len(rows)+len(chosen)<2 or d.proposal is None:fail()
            for r in rows:authorize('update',r)
            if any(r[k]!=target[k] for r in rows for k in CONDITIONS):fail('memory_merge_conditions')
            sources=[]
            for r in rows:sources=_merge(sources,stage.sources(r['memory_id'],r['version']))
            if chosen and not refs:fail('memory_direct_source_required')
            if refs:
                sources=_merge(sources,evidence.resolve_wiki(archive,namespace,caller,refs,indexes,now))
                if any(stage.suppressed(evidence.identity(s)) for s in sources):fail('memory_source_suppressed')
            p=d.proposal.model_dump();evidence.sensitive(p)
            if p['basis']=='user_declaration' and not any(s['user_origin'] for s in sources):fail('memory_user_source_required')
            if p['basis']!=target['basis'] or any(p[k]!=target[k] for k in CONDITIONS):fail('memory_merge_conditions')
            # Validate each new proposal independently: historical evidence cannot
            # establish a new proposal's basis, scope or lifetime.
            new_expiries=[];new_gaps=[];new_candidate=False
            for proposed in chosen:
                selected=[e for e in d.evidence_ids if e in proposed['evidence_ids']]
                if not selected:fail('memory_direct_source_required')
                localrefs=[source_map[e][0] for e in selected]
                localindexes=[i for i,e in enumerate(selected) if source_map[e][1]]
                validation=Staged(stage.writer)
                validation.matches=lambda *a:[]
                normalized=compile_wiki_request({'request':dict(action='record',scope=ledger.scope,
                    destination=dict(page_id=target['page_id'],expected_page_version=page(target['page_id'])['expected_version']),
                    proposal=proposed['proposal'],sources=localrefs,scope_source_indexes=localindexes)})
                commit(validation,archive,namespace,caller,normalized,settings,now)
                for item in validation.memories.values():
                    new_expiries.append(item['row']['expires_at'])
                    new_gaps.extend(json.loads(item['row']['gaps_json']))
                    new_candidate |= item['row']['status']=='candidate'
            dest=page(d.destination or target['page_id'])
            row=dict(target,page_id=dest['page_id'],version=target['version']+1,updated_at=now,title=p['title'],body=p['body'],
                aliases_json=json.dumps(p['aliases']),tags_json=json.dumps(p['tags']),
                expires_at=min([r['expires_at'] for r in rows]+new_expiries),evidence_at=min(r['evidence_at'] for r in rows),
                status='candidate' if new_candidate or any(r['status']=='candidate' for r in rows) or p['gaps'] or p['intent']!='activate' else target['status'])
            # Organization alone never extends clocks or removes uncertainty.
            row['gaps_json']=json.dumps(list(dict.fromkeys([g for r in rows for g in json.loads(r['gaps_json'])]+p['gaps']+new_gaps)))
            deadlines=[r['deadline_at'] for r in rows if r['deadline_at'] is not None]
            if p['deadline'] is not None:deadlines.append(p['deadline'])
            row['deadline_at']=min(deadlines) if deadlines else None
            if row['deadline_at'] is not None:row['expires_at']=min(row['expires_at'],row['deadline_at'])
            if row['expires_at']<=now:fail('memory_evidence_expired')
            if refs and d.confirmation_scope=='whole':
                validation=Staged(stage.writer)
                members={r['memory_id'] for r in rows}
                validation.matches=lambda *a:[r for r in stage.matches(*a) if r['memory_id'] not in members]
                normalized=compile_wiki_request({'request':dict(action='update',scope=ledger.scope,
                    memory_id=target['memory_id'],expected_version=target['version'],
                    change=dict(mode='replace',proposal=p),sources=refs,scope_source_indexes=indexes)})
                if not allowed(normalized):fail('memory_permission_denied')
                commit(validation,archive,namespace,caller,normalized,settings,now)
                renewed=validation.memories[target['memory_id']]['row']
                renewed.update(page_id=dest['page_id'])
                if row['deadline_at'] is not None:
                    renewed['deadline_at']=row['deadline_at'];renewed['expires_at']=min(renewed['expires_at'],row['deadline_at'])
                row=renewed
            stage.apply(memories=[dict(row=row,sources=sources,expected_version=target['version'])])
            for r in rows:
                if r['memory_id']==target['memory_id']:continue
                stage.apply(memories=[dict(row=dict(r,version=r['version']+1,updated_at=now,status='inactive'),
                    sources=stage.sources(r['memory_id'],r['version']),expected_version=r['version'])])
            continue
        if d.relation in ('supplement','confirm','correction'):
            if target is None or len(rows)!=1 or not refs:fail()
            mode={'supplement':'supplement','confirm':'confirm','correction':'replace'}[d.relation]
            if mode!='supplement' and d.confirmation_scope!='whole':fail('memory_partial_confirmation')
            change=dict(mode=mode)
            if mode=='replace':
                if d.proposal is None:fail()
                change['proposal']=d.proposal.model_dump()
            request=dict(action='update',scope=ledger.scope,memory_id=target['memory_id'],expected_version=target['version'],
                change=change,sources=refs,scope_source_indexes=indexes)
        else:
            if d.relation not in ('add','unresolved_conflict') or rows or not chosen or not refs or d.destination is None:fail()
            p=d.proposal.model_dump() if d.proposal else chosen[0]['proposal'] if len(chosen)==1 else None
            if p is None:fail()
            p=dict(p)
            if d.relation=='unresolved_conflict':p.update(intent='candidate',gaps=list(dict.fromkeys([*p['gaps'],'known_conflict'])))
            dest=page(d.destination)
            destination=dict(page_id=dest['page_id'],expected_page_version=dest['expected_version']) if dest['expected_version'] else dict(new_page={k:dest[k] for k in ('topic','title','description')})
            request=dict(action='record',scope=ledger.scope,destination=destination,proposal=p,sources=refs,scope_source_indexes=indexes)
        authorize(request['action'])
        # Exact parameters are checked as well as action/scope rules.
        normalized=compile_wiki_request({'request':request})
        if not allowed(normalized):fail('memory_permission_denied')
        if request['action']=='record' and dest['expected_version'] is None:
            # Reuse the program-assigned N page identity inside the existing compiler.
            original=stage.new_page
            stage.new_page=lambda **kw:dest
            try:commit(stage,archive,namespace,caller,normalized,settings,now)
            finally:stage.new_page=original
        else:commit(stage,archive,namespace,caller,normalized,settings,now)
    if len(consumed)!=len(set(consumed)) or set(consumed)!=set(proposals):fail('memory_proposal_coverage')
    for link in finished.links:
        authorize('update')
        origin=page(link.page);dirty.add(link.page)
        targets=[]
        for ref in link.targets:
            if ref in pages:targets.append(pages[ref])
            elif ref in ledger.discovered:targets.append(stage.page(ref))
            else:fail('memory_page_not_discovered')
        stage.links.append(dict(page_id=origin['page_id'],targets=[dict(scope=ledger.scope,page_id=t['page_id'],description='') for t in targets]))
    destinations={item['row']['page_id'] for item in stage.memories.values()}
    stage.apply(pages=[p for ref,p in pages.items() if ref in dirty or p['page_id'] in destinations])


def commit_group(access,caller,project_id,units,allowed):
    now=time.time();scope=units[0][0].scope
    if any(l.scope!=scope for l,_,_ in units):fail('memory_scope_mismatch')
    with access._lock:
        namespace=access._binding(caller,project_id,scope,'update')
        repo=access._stores(scope)[0][1]
        with repo.wiki_edit_with_sources(access.storage.memory,scope,namespace if scope=='workspace' else None,
                now=now,capacity=access.settings.wiki_topic_max_memories) as (writer,archive):
            stage=Staged(writer)
            for ledger,proposals,finished in units:
                info=writer.snapshot()
                if ledger.states is not None and [info['generation_id'],info['catalog_revision']]!=ledger.states or ledger.expiry is not None and ledger.expiry<=now:
                    fail('memory_conflict')
                _compile(stage,archive,namespace,caller,ledger,proposals,finished,access.settings,now,allowed)
            writer.apply(pages=list(stage.pages.values()),memories=list(stage.memories.values()),links=stage.links)
            return len(stage.memories)
