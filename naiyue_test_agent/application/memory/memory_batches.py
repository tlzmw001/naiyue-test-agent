"""Project saved text into bounded batches; never trust model source metadata."""


from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent.contracts.memory import MemoryReadError, MemoryBatch, BoundProposal
from .memory_sources import SECRET


from .memory_input_units import assemble, model_batch


def build_batch(memory_repo, *, session_id, thread_id, conversation_revision, start, upper, token_budget,
        source_chars, max_messages, max_read_chars, overlap_chars, context_floor) -> MemoryBatch:
    numbers = [conversation_revision, upper, token_budget, source_chars, max_messages,
        max_read_chars, overlap_chars, context_floor]
    if (not isinstance(start,(tuple,list)) or len(start)!=2
            or any(type(n) is not int or not 0<=n<=9223372036854775807 for n in [*start,*numbers])
            or not 1<=max_messages<=1024 or not 1024<=max_read_chars<=262144
            or not 0<=overlap_chars<=min(8192,max_read_chars//4)
            or not 1<=context_floor<=max(1,start[0]) or not token_budget or not source_chars
            or start[0]==0 and start[1]!=0):
        raise MemoryReadError('memory_batch_position_invalid')
    with memory_repo.batch_reader(SECRET) as archive:
        return assemble(archive,session_id=session_id,thread_id=thread_id,
            conversation_revision=conversation_revision,start=start,upper=upper,token_budget=token_budget,
            source_chars=source_chars,max_messages=max_messages,max_read_chars=max_read_chars,
            overlap_chars=overlap_chars,context_floor=context_floor)


def bind_proposal_evidence(proposal, batch: MemoryBatch) -> BoundProposal:
    from naiyue_test_agent.tools.memory_writes import MemorySource
    diagnostic='evidence_id_missing'
    try:
        references=proposal['sources']
        if not isinstance(references,list) or not 1<=len(references)<=8:
            raise ValueError('evidence_limit')
        ids=[]; evidence=[]; sources=[]
        for reference in references:
            ref=_Reference.model_validate(reference)
            if ref.evidence_id in ids:raise ValueError('duplicate_evidence')
            ids.append(ref.evidence_id)
            unit=batch['evidence_map'][ref.evidence_id]
            diagnostic='evidence_not_citable'
            if unit['context_only'] or unit['tool_pair_status'] in ('missing','invalid'):raise ValueError('context_only')
            source={k:unit[k] for k in ('session_id','thread_id','message_id','revision','conversation_revision')}
            source.update(quote=unit['text'],offset=unit['start_offset'],current_user=False)
            sources.append(MemorySource.model_validate(source).model_dump())
            evidence.append(unit)
        diagnostic='overlap_only'
        if not any(u['origin']=='new' for u in evidence):raise ValueError('overlap_only')
        context=[]; complete=True; used=0
        for unit in evidence:
            peers=[u for u in batch['units'] if u['message_key']==unit['message_key']]
            index=peers.index(unit)
            for neighbor,required in ((peers[index-1] if index else None,unit['partial_before']),
                    (peers[index+1] if index+1<len(peers) else None,unit['partial_after'])):
                if not required:continue
                if neighbor is None or neighbor['context_only']:
                    complete=False;continue
                if neighbor in evidence or neighbor in context:continue
                if used+len(neighbor['text'])>batch['context_char_limit']:
                    complete=False;continue
                context.append(neighbor);used+=len(neighbor['text'])
            if unit['tool_pair_status'] not in ('paired','not_tool'):complete=False
            if unit['role']=='tool':
                heads=[u for u in batch['units'] if u['role']=='ai' and u['context_only']
                    and u['tool_call_id']==unit['tool_call_id'] and u['message_id']==unit['parent_message_id']]
                if not heads:complete=False
                for head in heads:
                    if head not in context:context.append(head)
        return dict(proposal={k:v for k,v in proposal.items() if k!='sources'}, evidence_ids=ids,
            sources=sources,evidence=evidence,context_units=context,context_complete=complete)
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        failure=MemoryReadError('memory_proposal_invalid');failure._diagnostic_reason=diagnostic
        raise failure from error


class _Reference(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    evidence_id: str = Field(min_length=1, max_length=128)
