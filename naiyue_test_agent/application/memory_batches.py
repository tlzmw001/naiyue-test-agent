"""Project saved text into bounded batches; never trust model source metadata."""

import json
import logging
import math
import sqlite3
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from pydantic import BaseModel, ConfigDict, Field

from ..model_access.stop_reason import normalize_stop_reason
from ..tools.memory import MemoryInput
from ..tools.memory_writes import Kind, MemoryProposal
from . import history_projection
from .memory_queries import MemoryReadError
from .memory_sources import SECRET


logger = logging.getLogger(__name__)


def model_batch(batch):
    sources = []
    for source in batch.get('sources', []):
        visible = {key: source[key] for key in ('source_key', 'role', 'text', 'occurred_at', 'name', 'tool_status')}
        visible['partial'] = source['offset'] > 0 or source['offset'] + len(source['text']) < source['text_chars']
        sources.append(visible)
    return dict(sources=sources, complete=batch.get('complete', False), skipped=batch.get('skipped', []))


def _tokens(value, model_view=False):
    return count_tokens_approximately([HumanMessage(content=json.dumps(
        model_batch(value) if model_view else value, ensure_ascii=False))])


def _batch(connection, *, session_id, thread_id, conversation_revision, start, upper, token_budget, source_chars, model_view):
    saved = connection.execute('SELECT thread_id,conversation_revision,workspace_root FROM naiyue_sessions WHERE session_id=?',
        (session_id,)).fetchone()
    binding = connection.execute('SELECT root_path FROM naiyue_workspace WHERE singleton=1').fetchone()
    if saved is None or saved['thread_id'] != thread_id or saved['conversation_revision'] != conversation_revision:
        raise MemoryReadError('memory_source_changed')
    if binding is None or saved['workspace_root'] != binding[0]:
        raise MemoryReadError('memory_source_scope_mismatch')
    head = connection.execute("SELECT history_version FROM naiyue_chat_sync WHERE session_id=? AND thread_id=? AND checkpoint_ns=''",
        (session_id, thread_id)).fetchone()
    position = [max(start[0], 1), start[1]]
    result = dict(sources=[], next_position=position, complete=position[0] > upper, skipped=[],
        history_version=head['history_version'] if head else 0)
    if _tokens(result, model_view) > token_budget:
        raise MemoryReadError('memory_batch_budget')
    row = None
    while position[0] <= upper:
        sql = f"""SELECT {history_projection.FIELDS},m.occurred_at,
            naiyue_visible_length({history_projection.VISIBLE_TEXT}) AS text_chars,
            naiyue_memory_sensitive({history_projection.VISIBLE_TEXT}) AS sensitive
            FROM naiyue_chat_messages m WHERE {history_projection.VISIBLE_WHERE}
            AND m.sequence>=? AND m.sequence<=? ORDER BY m.sequence LIMIT 1"""
        if row is None or row['sequence'] != position[0]:
            row = connection.execute(sql, (session_id, thread_id, position[0], upper)).fetchone()
        if row is None:
            result.update(next_position=[upper + 1, 0], complete=True)
            if _tokens(result, model_view) > token_budget:
                raise MemoryReadError('memory_batch_budget')
            return result
        sequence, total = row['sequence'], row['text_chars']
        offset = position[1] if sequence == position[0] else 0
        if offset > total:
            raise MemoryReadError('memory_batch_position_invalid')
        reason = ('sensitive' if row['sensitive'] else 'time_unavailable' if row['occurred_at'] is None
            or not math.isfinite(row['occurred_at']) else 'empty' if offset == total else None)
        content = ''
        if reason is None:
            visible = history_projection.message(connection, session_id, thread_id, sequence,
                offset=offset, chars=source_chars)
            content = visible['content']
        source = dict(source_key=f'{sequence}:{offset}', session_id=session_id, thread_id=thread_id,
            message_id=row['message_id'], revision=row['revision'], conversation_revision=conversation_revision,
            role=row['message_type'], reply_id=row['reply_id'], tool_call_id=row['tool_call_id'],
            occurred_at=row['occurred_at'], offset=offset, text_chars=total,
            name=row['name'], tool_status=row['tool_status'])
        low, high = 0, len(content)
        while low < high:
            end = (low + high + 1) // 2
            next_position = [sequence + 1, 0] if offset + end == total else [sequence, offset + end]
            proposed = dict(result, sources=[*result['sources'], dict(source, text=content[:end])],
                next_position=next_position, complete=next_position[0] > upper)
            if _tokens(proposed, model_view) <= token_budget:
                low = end
            else:
                high = end - 1
        if low:
            position = [sequence + 1, 0] if offset + low == total else [sequence, offset + low]
            result.update(sources=[*result['sources'], dict(source, text=content[:low])],
                next_position=position, complete=position[0] > upper)
            if low < len(content):
                return result
            continue
        if reason is None and result['sources']:
            result.update(next_position=[sequence, offset], complete=False)
            return result
        position = [sequence + 1, 0]
        proposed = dict(result, skipped=[*result['skipped'], f'{reason or "envelope_too_large"}:{sequence}'],
            next_position=position, complete=position[0] > upper)
        if _tokens(proposed, model_view) > token_budget:
            if result['sources'] or result['skipped']:
                return result
            raise MemoryReadError('memory_batch_budget')
        result = proposed
    return result


def build_batch(storage, *, session_id, thread_id, conversation_revision, start, upper, token_budget, source_chars,
        model_view=False):
    numbers = [conversation_revision, upper, token_budget, source_chars]
    if (not isinstance(start, (tuple, list)) or len(start) != 2
            or any(type(n) is not int or not 0 <= n <= 9223372036854775807 for n in [*start, *numbers])
            or token_budget == 0 or source_chars == 0 or start[0] == 0 and start[1] != 0):
        raise MemoryReadError('memory_batch_position_invalid')
    try:
        with storage._lock, storage.connection:
            # Check the complete visible source before slicing: a secret may be in a later chunk.
            # The callback returns only a boolean; raw message JSON/hidden fields never leave SQL.
            storage.connection.create_function('naiyue_memory_sensitive', 1,
                lambda text: bool(SECRET.search(text)), deterministic=True)
            storage.connection.execute('BEGIN')
            return _batch(storage.connection, session_id=session_id, thread_id=thread_id,
                conversation_revision=conversation_revision, start=start, upper=upper,
                token_budget=token_budget, source_chars=source_chars, model_view=model_view)
    except sqlite3.Error as error:
        logger.error('memory_batch_read_failed error_type=%s', type(error).__name__)
        raise MemoryReadError('memory_batch_read_failed') from error


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
    source_key: str = Field(min_length=1, max_length=128)
    quote: str = Field(min_length=1, max_length=300)


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


def proposal_request(proposal, sources, related=()):
    try:
        item = _Proposal.model_validate(proposal)
        old = None
        if item.memory_id is not None:
            matches = [row for row in related if row['memory_id'] == item.memory_id]
            if len(matches) != 1 or matches[0].get('complete') is not True:
                raise ValueError('Update requires one complete record actually sent to the model')
            old = matches[0]
            if item.kind != old['kind'] or item.scope is not None and item.scope != old['scope']:
                raise ValueError('Update cannot change scope or kind')
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
        by_key = {source['source_key']: source for source in sources}
        if len(by_key) != len(sources):
            raise ValueError('Ambiguous batch source identity')
        references = []
        for reference in item.sources:
            source = by_key[reference.source_key]
            offset = source['text'].find(reference.quote)
            if offset < 0 or source['text'].find(reference.quote, offset + 1) >= 0:
                raise ValueError('Quote must uniquely match a current batch source')
            references.append({key: source[key] for key in ('session_id', 'thread_id', 'message_id',
                'revision', 'conversation_revision')} | dict(quote=reference.quote, offset=source['offset'] + offset))
        return MemoryInput(action='update' if old else 'record', scope=scope, proposal=payload,
            sources=references, memory_id=item.memory_id,
            expected_version=old['version'] if old else None).model_dump(mode='json')
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise MemoryReadError('memory_proposal_invalid') from error
