"""Extraction/comparison contracts and program-owned decision compilation."""
import json
import logging
from typing import Annotated, Literal
from pydantic import BaseModel,ConfigDict,Field
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.contracts import memory_lexical as lexical
from naiyue_test_agent.tools.memory import MemoryInput
from naiyue_test_agent.tools.memory_writes import Kind,MemoryProposal
from .memory_batches import bind_proposal_evidence,_Reference
from .memory_model_json import _content,_object,_reject_constant
from .memory_sources import sensitive
from naiyue_test_agent.diagnostics import safe_log

Short=Annotated[str,Field(min_length=1,max_length=128)]
logger=logging.getLogger(__name__)


class Claim(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    kind:Kind|None=Field(default=None,description='Inherited by the program; omit this redundant field in comparison claims.')
    subject:Short
    body:str=Field(min_length=1,max_length=3200)
    basis:Literal['user_declaration','tool_observation','inference']
    intent:Literal['candidate','activate']
    title:str|None=Field(default=None,min_length=1,max_length=160)
    reason:str|None=Field(default=None,min_length=1,max_length=300)
    machine:Short|None=None
    module:Short|None=None
    target:Short|None=None
    aliases:list[Short]|None=Field(default=None,max_length=5)
    gaps:list[Short]|None=Field(default=None,max_length=8)


class Extraction(Claim):
    kind:Kind
    body:str=Field(min_length=1,max_length=800)
    matter:Short
    identity_terms:list[Annotated[str,Field(min_length=1,max_length=64)]]=Field(default_factory=list,max_length=4)
    sources:list[_Reference]=Field(min_length=1,max_length=8)
    scope:Literal['workspace','personal']='workspace'


class Overview(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    goal:str
    progress:str
    blockers:str
    next_step:str


class ExtractionResult(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',title='MemoryExtractionResult')
    overview:Overview
    proposals:list[Extraction]=Field(max_length=8)


class Decision(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    proposal_ids:list[Short]=Field(min_length=1,max_length=8)
    relation:Literal['new','duplicate','supplement','correction','different_conditions','unresolved_conflict','unsupported']
    target_old_ref:Short|None
    support:Literal['direct','inference','unsupported']
    confirmation_scope:Literal['whole','partial','unknown']
    claim:Claim|None
    new_evidence_ids:list[Short]=Field(max_length=8)
    old_evidence_refs:list[Short]=Field(max_length=8)
    reason:str=Field(min_length=1,max_length=300)
    gaps:list[Short]=Field(max_length=8)


class ComparisonGroup(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    group_id:Short
    decisions:list[Decision]=Field(max_length=8)


class ComparisonResult(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',title='MemoryComparisonResult')
    groups:list[ComparisonGroup]=Field(max_length=8)


def parse_stage(response,settings,*,stage,output_mode='text'):
    if stage not in ('extract','compare') or output_mode not in ('text','json_schema','function_calling'):
        raise MemoryReadError('memory_result_invalid')
    name='MemoryExtractionResult' if stage=='extract' else 'MemoryComparisonResult'
    try:
        value=json.loads(_content(response,output_mode,expected_name=name),object_pairs_hook=_object,parse_constant=_reject_constant)
        key='proposals' if stage=='extract' else 'groups'
        if (not isinstance(value,dict) or set(value)!=({'overview','proposals'} if stage=='extract' else {'groups'})
                or not isinstance(value[key],list) or len(value[key])>settings.max_proposals):
            raise ValueError('root')
        return value
    except MemoryReadError:raise
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError) as error:
        raise MemoryReadError('memory_result_invalid' if stage=='extract' else 'memory_comparison_output_invalid') from error


def validate_overview(value,settings):
    try:
        value=Overview.model_validate(value).model_dump()
        if sum(map(len,value.values()))>settings.overview_chars:raise ValueError('budget')
        sensitive(value)
        return value
    except ValueError as error:raise MemoryReadError('memory_overview_invalid') from error


def bind_extraction(raw,batch,index):
    try:
        item=Extraction.model_validate(raw)
        values=item.model_dump(exclude_none=True)
        if not item.matter.strip() or any(not t.strip() for t in item.identity_terms):raise ValueError('blank')
        # Validate the public content contract before any old-state query.
        MemoryProposal.model_validate({k:v for k,v in values.items() if k in Claim.model_fields}
            |dict(title=item.title or item.subject,reason=item.reason or 'Background extraction'))
        bound=bind_proposal_evidence(values,batch)
        anchors=set().union(*(lexical.anchors(u['text']) for u in bound['evidence']))
        if len(set(item.identity_terms))!=len(item.identity_terms) or not set(item.identity_terms)<=anchors:
            raise ValueError('identity')
        sensitive(values)
        return dict(bound,proposal_id=f'P{index}',matter=item.matter,identity_terms=item.identity_terms,scope=item.scope)
    except MemoryReadError:raise
    except (ValueError,TypeError,KeyError) as error:
        failure=MemoryReadError('memory_proposal_invalid');failure._diagnostic_reason='fields_invalid'
        raise failure from error


def compile_comparison_decisions(raw,group,proposals,old):
    """Validate the entire group before returning any write request."""
    try:
        parsed=ComparisonGroup.model_validate(raw)
        if parsed.group_id!=group['group_id']:raise ValueError('group')
        consumed=[];targets=set();writes=[]
        for d in parsed.decisions:
            ids=d.proposal_ids
            if len(set(ids))!=len(ids) or not set(ids)<=set(group['proposal_ids']):raise ValueError('proposal')
            consumed.extend(ids)
            inputs=[proposals[p] for p in ids]
            scopes={p['scope'] for p in inputs};kinds={p['proposal']['kind'] for p in inputs}
            source_only=d.relation=='supplement' and d.confirmation_scope in ('partial','unknown')
            if len(scopes)!=1 or len(kinds)!=1 and not (source_only or d.relation in ('duplicate','unsupported')):
                raise ValueError('merged_scope_kind')
            sources={eid:(u,s) for p in inputs for eid,u,s in zip(p['evidence_ids'],p['evidence'],p['sources'])}
            if len(set(d.new_evidence_ids))!=len(d.new_evidence_ids) or not set(d.new_evidence_ids)<=sources.keys():raise ValueError('evidence')
            if d.target_old_ref is not None and d.target_old_ref not in group['old_refs']:raise ValueError('target')
            allowed_old={s['old_evidence_ref'] for key in group['old_refs'] for s in old[key]['sources']}
            if source_only:
                if d.old_evidence_refs:
                    safe_log(logger.info,'memory_comparison_content_ignored relation=%s field=old_evidence_refs',d.relation)
            elif len(set(d.old_evidence_refs))!=len(d.old_evidence_refs) or not set(d.old_evidence_refs)<=allowed_old:
                raise ValueError('old_source')
            updating=d.relation in ('supplement','correction','duplicate')
            if updating!=(d.target_old_ref is not None):raise ValueError('target_required')
            target=old[d.target_old_ref] if updating else None
            if target and target['scope'] not in scopes:raise ValueError('target_scope_kind')
            ignored=('claim' if d.claim is not None and (source_only or d.relation in ('duplicate','unsupported'))
                else 'kind' if d.claim is not None and d.claim.kind is not None else None)
            if ignored:
                safe_log(logger.info,'memory_comparison_content_ignored relation=%s field=%s',d.relation,ignored)
            if d.relation in ('duplicate','unsupported'):
                continue
            if d.support=='unsupported' or not d.new_evidence_ids:raise ValueError('unsupported')
            if target and d.target_old_ref in targets:raise ValueError('duplicate_target')
            if target:targets.add(d.target_old_ref)
            evidence=[sources[e][0] for e in d.new_evidence_ids]
            if not any(u['origin']=='new' for u in evidence):raise ValueError('overlap_only')
            if source_only:
                if d.support!='direct':raise ValueError('source_only_support')
                values={k:target[k] for k in Claim.model_fields if k in target}
                values.update(reason=d.reason,intent='activate' if target['status']=='active' else 'candidate')
            elif d.claim is None:
                if len(inputs)!=1 or set(d.new_evidence_ids)!=set(inputs[0]['evidence_ids']):
                    raise ValueError('claim_reuse_requires_one_complete_proposal')
                values={k:v for k,v in inputs[0]['proposal'].items() if k in Claim.model_fields}
            else:
                values=d.claim.model_dump(exclude_none=True)
            if not source_only:values['kind']=next(iter(kinds))
            if target:
                for k in ('title','reason','aliases','gaps','machine','module','target'):
                    values.setdefault(k,target[k] if k in target else target['conditions'][k])
                values['tags']=target['tags']
            if target and target['kind']!=values['kind']:
                if (d.relation!='correction' or d.support!='direct' or d.confirmation_scope!='whole'
                        or values['basis']!='user_declaration'
                        or not any(u.get('user_origin') and u['occurred_at']>target['evidence_at'] for u in evidence)):
                    raise ValueError('kind_change_confirmation')
            if d.relation=='correction':
                matched=[u for u in evidence if (values['basis']=='user_declaration' and u.get('user_origin')
                    or values['basis']=='tool_observation' and values.get('target') and u.get('tool_status')=='success')]
                if d.confirmation_scope!='whole' or not any(u['occurred_at']>target['evidence_at'] for u in matched):
                    raise ValueError('correction_time')
            values.setdefault('title',values['subject']);values.setdefault('reason',d.reason)
            if not source_only:values['gaps']=list(dict.fromkeys([*values.get('gaps',[]),*d.gaps]))
            if d.support=='inference' or values['basis']=='inference':values['intent']='candidate'
            if d.relation=='unresolved_conflict':
                values['intent']='candidate';values['gaps']=list(dict.fromkeys([*values['gaps'],'unresolved_conflict']))
            request=MemoryInput(action='update' if target else 'record',scope=next(iter(scopes)),
                memory_id=target['memory_id'] if target else None,expected_version=target['version'] if target else None,
                proposal=MemoryProposal.model_validate(values),sources=[sources[e][1] for e in d.new_evidence_ids]).model_dump(mode='json')
            sensitive(request)
            writes.append((request,d.confirmation_scope))
        if len(consumed)!=len(set(consumed)) or set(consumed)!=set(group['proposal_ids']):raise ValueError('coverage')
        return writes
    except MemoryReadError:raise
    except (ValueError,KeyError,TypeError,AttributeError) as error:
        raise MemoryReadError('memory_comparison_output_invalid') from error
