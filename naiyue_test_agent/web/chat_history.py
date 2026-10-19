"""Bounded history wire format and scope-bound pagination cursors."""

import base64
import hashlib
import hmac
import json

from langchain_core.messages import messages_from_dict
from naiyue_test_agent.model_access.messages import content_text

from .errors import WebError
from .serialization import encoded_size, message_view


def cursor_token(settings, value):
    payload = base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')
    signature = hmac.new(settings.access_token.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + signature


def read_cursor(settings, token, session_id, agent_id):
    try:
        payload, signature = token.split('.')
        expected = hmac.new(settings.access_token.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError('signature')
        value = json.loads(base64.b64decode(payload + '=' * (-len(payload) % 4), altchars=b'-_', validate=True))
        if (not isinstance(value, list) or len(value) != 6 or value[:3] != [1, session_id, agent_id]
                or any(type(n) is not int or n < 0 for n in value[3:]) or not 0 < value[3] <= value[4] + 1):
            raise ValueError('scope')
        return value
    except (ValueError, TypeError, UnicodeError):
        raise WebError('invalid_history_cursor', 422) from None


def bounded_message(row, settings, budget):
    value = {**row, 'content': content_text(row.get('content', ''))}
    if encoded_size(value) <= budget:
        return value
    if value.get('reasoning'):
        value['reasoning'] = value['reasoning'][:settings.history_preview_chars]
        value['reasoning_truncated'] = True
    if value.get('file_change'):
        change = value['file_change']
        value['file_change'] = {**change, 'diff': '', 'preview': '', 'diff_truncated': True, 'preview_truncated': True}
    value.update(content=value['content'][:settings.history_preview_chars], content_truncated=True)
    if value.get('provisional'):
        value['content_unavailable'] = True
    if value.get('tool_calls'):
        value['tool_calls'] = [{**c, 'args': {}} for c in value['tool_calls']]
        value['arguments_truncated'] = True
    while encoded_size(value) > budget and value.get('reasoning'):
        value['reasoning'] = value['reasoning'][:len(value['reasoning']) // 2]
    while encoded_size(value) > budget and value['content']:
        value['content'] = value['content'][:len(value['content']) // 2]
    while encoded_size(value) > budget and value.get('tool_calls'):
        value['tool_calls'] = value['tool_calls'][:-1]
        value['tool_calls_truncated'] = True
    if encoded_size(value) > budget:
        raise WebError('history_message_unavailable', 503)
    return value


def recent_rows(rows, settings, *, budget=None):
    budget = budget if budget is not None else min(settings.history_page_bytes, settings.max_frame_bytes // 3)
    selected, used = [], 2
    for row in reversed(rows[-settings.history_page_messages:]):
        value = bounded_message(row, settings, min(settings.history_message_bytes, budget - 2))
        size = encoded_size(value) + 1
        if selected and used + size > budget:
            break
        selected.append(value)
        used += size
    return list(reversed(selected))


def history_window(page, live_rows, settings, session_id, agent_id=None, *, restore_saved_drafts=False):
    archived = {row['id']: row for row in page['messages']}
    messages = dict(archived)
    for row in live_rows:
        if row.get('identity_only'):
            continue
        saved = archived.get(row['id'], {})
        if restore_saved_drafts and saved and row.get('provisional'):
            messages[row['id']] = {**saved, **{key: row[key] for key in ('session_id', 'run_id') if key in row}}
            continue
        messages[row['id']] = {**row, **{key: saved[key] for key in ('sequence', 'revision', 'detail') if key in saved},
                               'parent_message_id': row.get('parent_message_id') or saved.get('parent_message_id')}
    rows = recent_rows(list(messages.values()), settings)
    visible = {row['id'] for row in rows}
    more = page['has_more'] or any(identity not in visible for identity in archived)
    position = min((row['sequence'] for row in page['messages'] if row['id'] in visible),
                   default=page['coverage']['upper'] + 1)
    token = cursor_token(settings, [1, session_id, agent_id, position, page['coverage']['upper'],
                                   page['coverage']['history_version']]) if more else None
    return {**page, 'messages': rows, 'has_more': more, 'next_cursor': token}


def record_view(record):
    if record['body_json'] is not None:
        value = message_view(messages_from_dict([json.loads(record['body_json'])])[0])
    else:
        value = dict(id=record['message_id'], type=record['message_type'], content=record['preview'],
                     is_context_summary=record['kind'] == 'context_summary', content_truncated=True,
                     provisional=False, state='complete')
        if record.get('reasoning_preview'):
            value.update(reasoning=record['reasoning_preview'], reasoning_truncated=True)
        if record['message_type'] == 'tool':
            if record.get('file_change_json'):
                from naiyue_test_agent.tools.file_change import change_projection
                change = change_projection(json.loads(record['file_change_json']))
                if change is not None:
                    value['file_change'] = change
            value.update(tool_call_id=record['tool_call_id'], name=record['name'], status=record['status'])
        elif record['message_type'] == 'ai':
            value['tool_calls'] = [{**call, 'args': {}} for call in record.get('calls', [])]
            value['arguments_truncated'] = bool(record['calls'])
            value['tool_calls_truncated'] = record['tool_calls_truncated']
    return {**value, 'reply_id': record.get('reply_id'), 'sequence': record['sequence'], 'revision': record['revision'],
            'parent_message_id': record['parent_message_id'],
            'detail': {'sequence': record['sequence'], 'revision': record['revision']}}


async def history_page(application, settings, session_id, *, agent_id=None, before=None, limit=None):
    count = min(limit or settings.history_page_messages, settings.history_page_messages)
    budget = min(settings.history_page_bytes, settings.max_frame_bytes // 3)
    query = {}
    if before:
        _, _, _, position, upper, version = read_cursor(settings, before, session_id, agent_id)
        query = dict(before=position, upper=upper, version=version)
    page = await application.aget_chat_page(session_id, agent_id=agent_id, limit=count,
        body_bytes=min(settings.history_message_bytes, max(1, budget // 2)),
        preview_chars=min(settings.history_preview_chars, max(1, budget // 8)), **query)
    selected, used = [], 2
    for record in page['records']:
        row = bounded_message(record_view(record), settings, min(settings.history_message_bytes, budget - 2))
        size = encoded_size(row) + 1
        if selected and used + size > budget:
            break
        selected.append(row)
        used += size
    more = page['has_more'] or len(selected) < len(page['records'])
    next_cursor = cursor_token(settings, [1, session_id, agent_id, selected[-1]['sequence'], page['upper'],
                                          page['history_version']]) if more and selected else None
    coverage = {key: page[key] for key in ('checkpoint_id', 'sync_revision', 'history_version', 'upper')}
    return {'messages': list(reversed(selected)), 'has_more': more, 'next_cursor': next_cursor,
            'coverage': coverage, 'has_compaction': page['has_compaction']}
