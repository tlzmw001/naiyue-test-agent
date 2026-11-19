"""Test-only construction of extraction plus program-owned comparison snapshots.

Legacy write-contract cases now exercise the controlled compiler; no product adapter
accepts an old flat proposal's memory_id. Runner tests supply explicit A/B responses.
"""
from naiyue_test_agent.application.memory.memory_proposals import (bind_extraction,
    compile_comparison_decisions,parse_stage,validate_overview)
from naiyue_test_agent.contracts.memory import MemoryReadError


def invalid(detail):
    error=MemoryReadError('memory_proposal_invalid');error._diagnostic_reason=detail
    return error


def compile_request(proposal,batch,related=()):
    raw=dict(proposal);target=raw.pop('memory_id',None);old=None
    if target:
        matches=[r for r in related if r['memory_id']==target]
        if len(matches)!=1 or not matches[0].get('complete'):raise invalid('update_target_unavailable')
        old=dict(matches[0]);scope=raw.get('scope') or old['scope']
        if scope!=old['scope'] or raw['kind']!=old['kind']:raise invalid('update_scope_or_kind')
        raw['scope']=scope
    if raw.get('scope') is None:raw.pop('scope',None)
    raw.setdefault('matter',raw.get('subject','invalid'))
    bound=bind_extraction(raw,batch,1)
    refs=['O1'] if old else []
    if old:
        old.setdefault('sources',[]);old.setdefault('tags',[]);old.setdefault('deadline_at',None)
        old['sources']=[dict(s,old_evidence_ref=f'O1:S{i}') for i,s in enumerate(old['sources'],1)]
        for k in ('subject','machine','module','target'):old.setdefault(k,old['conditions'].get(k))
    claim={k:v for k,v in bound['proposal'].items() if k not in ('matter','identity_terms','scope')}
    decision=dict(proposal_ids=['P1'],relation='supplement' if old else 'new',target_old_ref='O1' if old else None,
        support='direct',confirmation_scope='whole',claim=claim,new_evidence_ids=bound['evidence_ids'],
        old_evidence_refs=[],reason='Test comparison',gaps=[])
    group=dict(group_id='G1',proposal_ids=['P1'],old_refs=refs)
    return compile_comparison_decisions(dict(group_id='G1',decisions=[decision]),group,{'P1':bound},{'O1':old} if old else {})[0][0]


def parse_extraction(response,settings,*,output_mode='text'):
    result=parse_stage(response,settings,stage='extract',output_mode=output_mode)
    try:result['overview']=validate_overview(result['overview'],settings)
    except MemoryReadError as error:raise MemoryReadError('memory_result_invalid') from error
    return result


def comparison_response(proposals,*,relation='new',old_ref=None):
    from langchain_core.messages import AIMessage
    import json
    decisions=[]
    for i,p in enumerate(proposals,1):
        if not isinstance(p,dict) or not all(k in p for k in ('kind','subject','body','basis','intent','sources')):continue
        decisions.append(dict(group_id=f'G{len(decisions)+1}',decisions=[dict(proposal_ids=[f'P{i}'],relation=relation,
            target_old_ref=old_ref,support='direct',confirmation_scope='whole',
            claim={k:v for k,v in p.items() if k not in ('sources','scope','matter','identity_terms','memory_id')},
            new_evidence_ids=[s['evidence_id'] for s in p['sources']],old_evidence_refs=[],reason='Test comparison',gaps=[])]))
    return AIMessage(content=json.dumps(dict(groups=decisions),ensure_ascii=False))


def staged_replies(replies):
    """Explicitly script B for deterministic legacy worker scenarios."""
    import json
    expanded=[]
    for reply in replies:
        expanded.append(reply)
        try:value=json.loads(reply.content)
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and value.get('proposals'):
            expanded.append(comparison_response(value['proposals']))
    return expanded
