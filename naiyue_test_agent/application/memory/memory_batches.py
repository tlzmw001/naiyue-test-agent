"""Project saved text into bounded batches; never trust model source metadata."""

import json
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent.model_access.stop_reason import normalize_stop_reason
from naiyue_test_agent.tools.memory import MemoryInput
from naiyue_test_agent.tools.memory_writes import Kind, MemoryProposal
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


class _Overview(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    goal: str
    progress: str
    blockers: str
    next_step: str


class _Result(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    overview: _Overview
    proposals: list[object]


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError('Non-finite JSON number')


def _content(response, output_mode):
    if isinstance(response, AIMessage):
        stop = normalize_stop_reason(response)
        if stop == 'length':
            raise MemoryReadError('memory_result_truncated')
        if (stop == 'error' or response.invalid_tool_calls or response.additional_kwargs.get('refusal')
                or isinstance(response.content, list) and any(isinstance(b, dict) and b.get('type') == 'refusal'
                    for b in response.content)):
            raise MemoryReadError('memory_result_invalid')
        if output_mode == 'function_calling':
            if len(response.tool_calls) != 1 or response.tool_calls[0]['name'] != 'MemoryResult':
                raise MemoryReadError('memory_result_invalid')
            call = response.tool_calls[0]
            raw = response.additional_kwargs.get('tool_calls')
            if raw is not None:
                if (not isinstance(raw, list) or len(raw) != 1 or raw[0].get('id') != call['id']
                        or raw[0].get('function', {}).get('name') != 'MemoryResult'):
                    raise MemoryReadError('memory_result_invalid')
                return raw[0]['function']['arguments']
            return json.dumps(call['args'], ensure_ascii=False, allow_nan=False)
        if stop == 'tool_use' or response.tool_calls:
            raise MemoryReadError('memory_result_invalid')
        content = response.content
        if isinstance(content, list):
            content = ''.join(b if isinstance(b, str) else b.get('text', '')
                if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str) else ''
                for b in content)
        return content
    if output_mode != 'text':
        raise MemoryReadError('memory_result_invalid')
    return response


def parse_model_result(response, settings, *, output_mode='text'):
    try:
        if output_mode not in ('text', 'json_schema', 'function_calling'):
            raise ValueError('Unknown memory output protocol')
        content = _content(response, output_mode)
        if not isinstance(content, str):
            raise ValueError('Expected JSON text')
        parsed = json.loads(content, object_pairs_hook=_object, parse_constant=_reject_constant)
        result = _Result.model_validate(parsed)
        if (sum(len(v) for v in result.overview.model_dump().values()) > settings.overview_chars
                or len(result.proposals) > settings.max_proposals):
            raise ValueError('Memory result budget exceeded')
        return result.model_dump()
    except MemoryReadError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise MemoryReadError('memory_result_invalid') from error


class _Reference(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    evidence_id: str = Field(min_length=1, max_length=128)


class _Proposal(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    kind: Kind
    subject: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=800)
    basis: Literal['user_declaration', 'tool_observation', 'inference']
    intent: Literal['candidate', 'activate']
    sources: list[_Reference] = Field(min_length=1, max_length=8)
    scope: Literal['workspace', 'personal'] | None = None
    memory_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    title: str | None = Field(default=None, min_length=1, max_length=160)
    reason: str | None = Field(default=None, min_length=1, max_length=300)
    machine: str | None = Field(default=None, min_length=1, max_length=128)
    module: str | None = Field(default=None, min_length=1, max_length=128)
    target: str | None = Field(default=None, min_length=1, max_length=128)
    aliases: list[str] | None = Field(default=None, max_length=5)
    gaps: list[str] | None = Field(default=None, max_length=8)


class _StructuredResult(_Result):
    model_config = ConfigDict(strict=True, extra='forbid', title='MemoryResult')
    proposals: list[_Proposal] = Field(max_length=8)


def response_schema():
    return _StructuredResult.model_json_schema()


def proposal_request(proposal, batch, related=()):
    diagnostic = 'fields_invalid'
    try:
        item = _Proposal.model_validate(proposal)
        old = None
        if item.memory_id is not None:
            diagnostic = 'update_target_unavailable'
            matches = [row for row in related if row['memory_id'] == item.memory_id]
            if len(matches) != 1 or matches[0].get('complete') is not True:
                raise ValueError('Update requires one complete record actually sent to the model')
            old = matches[0]
            diagnostic = 'update_scope_or_kind'
            if item.kind != old['kind'] or item.scope is not None and item.scope != old['scope']:
                raise ValueError('Update cannot change scope or kind')
        diagnostic = 'fields_invalid'
        scope = old['scope'] if old else item.scope or 'workspace'
        values = item.model_dump(exclude={'scope', 'memory_id', 'sources'}, exclude_none=True)
        if old:
            for field in ('title', 'reason', 'aliases', 'gaps'):
                values.setdefault(field, old[field])
            for field in ('machine', 'module', 'target'):
                values.setdefault(field, old['conditions'][field])
            values['tags'] = old['tags']
        else:
            values.setdefault('title', item.subject)
            values.setdefault('reason', f'Background proposal: {item.basis}/{item.intent}; source and lifecycle gates apply.')
        payload = MemoryProposal.model_validate(values)
        references = bind_proposal_evidence(proposal, batch)['sources']
        diagnostic = 'write_request_invalid'
        return MemoryInput(action='update' if old else 'record', scope=scope, proposal=payload,
            sources=references, memory_id=item.memory_id,
            expected_version=old['version'] if old else None).model_dump(mode='json')
    except MemoryReadError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        failure = MemoryReadError('memory_proposal_invalid')
        # Internal diagnostic only: preserve the public error type/code and never copy model input.
        failure._diagnostic_reason = diagnostic
        raise failure from error
