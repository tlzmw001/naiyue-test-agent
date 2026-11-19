"""Bounded internal comparison candidates with existing admission and source gates."""
import json
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.contracts import memory_lexical as lexical
from naiyue_test_agent.tools.memory import MemoryInput


def candidates(access,namespace,caller,plan,allowed,now):
    scope=plan['scope'];queries=plan['queries'];settings=access.settings
    if scope not in ('workspace','personal') or not 1<=len(queries)<=2:
        raise MemoryReadError('memory_proposal_invalid')
    limit=min(settings.related_limit,settings.max_limit)
    if not limit:raise MemoryReadError('memory_comparison_limited')
    _,store=access._stores(scope)[0]
    if plan.get('sources'):
        from .memory_sources import resolve,identity,SECRET
        with access.storage.memory.batch_reader(SECRET) as archive:
            resolved=resolve(archive,namespace,caller,plan['sources'],now)
        if store.any_suppressed(identity(source) for source in resolved):
            raise MemoryReadError('memory_source_suppressed')
    ranked={};limited=False
    def failure(code):
        error=MemoryReadError(code)
        error._comparison_old_ids=tuple((scope,k) for k in ranked)
        return error
    try:
        for query in queries:
            if not query['text'].strip() or len(query['text'])>settings.max_query_chars:
                raise MemoryReadError('memory_query_too_complex')
            for status in ('active','candidate','expired'):
                request=MemoryInput(action='search',scope=scope,status=status,query=query['text'],limit=limit).model_dump(mode='json')
                if not allowed(request):raise MemoryReadError('memory_permission_denied')
            rows,more=store.rank_comparison(scope,namespace,now,query['text'],query['required'],settings)
            limited|=more
            for rank,row in enumerate(rows,1):
                key=row['memory_id']
                if key in ranked and ranked[key]['row']['version']!=row['version']:
                    raise failure('memory_version_changed')
                entry=ranked.setdefault(key,dict(row=row,score=0.))
                entry['score']+=1/(settings.rrf_k+rank)
        ordered=sorted(ranked.values(),key=lambda e:(-e['score'],e['row']['memory_id']))
        limited|=len(ordered)>limit
        if limited:raise failure('memory_comparison_limited')
        result=[]
        for entry in ordered:
            row=entry['row']
            if len(row['body'])>3200 or len(row['body'].encode())>3200:raise failure('memory_comparison_limited')
            expired=row['expires_at'] is not None and row['expires_at']<=now
            request=MemoryInput(action='read',scope=scope,status='expired' if expired else row['status'],
                memory_id=row['memory_id'],version=row['version']).model_dump(mode='json')
            if not allowed(request):raise failure('memory_permission_denied')
            current=store.current(scope,namespace,request,now,row['memory_id'])
            if current is None or current['version']!=row['version'] or lexical.digest(current['title'],current['aliases_json'],current['body'])!=lexical.digest(row['title'],row['aliases_json'],row['body']):
                raise failure('memory_version_changed')
            sources,more=store.sources(row['memory_id'],row['version'],8)
            if more:raise failure('memory_comparison_limited')
            result.append(dict(row,expired=expired,conditions={k:row[k] for k in ('machine','module','target','subject')},
                aliases=json.loads(row['aliases_json']),tags=json.loads(row['tags_json']),gaps=json.loads(row['gaps_json']),
                sources=[dict(access.storage.conversation.source_reference(namespace,s,detail=True),
                    evidence_format=s['evidence_format'],verified_at=s['verified_at'],
                    user_origin=None if s['user_origin'] is None else bool(s['user_origin']),
                    tool_success=None if s['tool_success'] is None else bool(s['tool_success'])) for s in sources]))
        return dict(rows=result,limited=False)
    except Exception as error:
        if not hasattr(error,'_comparison_old_ids'):
            error._comparison_old_ids=tuple((scope,k) for k in ranked)
        raise
