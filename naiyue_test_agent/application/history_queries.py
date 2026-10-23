"""Read-only archive operations, always inside the owning storage transaction."""

import base64
import binascii
import json
from hashlib import sha256

from .errors import SessionPersistenceError
from . import history_projection as projection
from .memory_overview_store import read_overview


def _fail(code):
    raise SessionPersistenceError(code)


def _encode(value):
    return base64.urlsafe_b64encode(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).decode()


def _decode(token, expected):
    if token is None:
        return None
    try:
        value = json.loads(base64.b64decode(token, altchars=b'-_', validate=True))
        if not isinstance(value, dict) or any(value.get(k) != v for k, v in expected.items()):
            raise ValueError('scope')
        return value
    except (ValueError, UnicodeError, binascii.Error, TypeError):
        _fail('history_cursor_stale')


def _size(result):
    return len(json.dumps(result, ensure_ascii=False).encode())


def _fit_record(result, item, budget):
    result['items'].append(item)
    if _size(result) <= budget:
        return True
    if len(result['items']) > 1:
        result['items'].pop()
        return False
    while item.get('content') and _size(result) > budget:
        item['content'] = item['content'][:len(item['content']) // 2]
        projection.finish_record(item)
    if _size(result) > budget or (item.get('text_chars') and not item.get('content')):
        _fail('history_output_budget')
    return True


def _directory(storage, source, request, settings, count):
    expected = dict(v=1, action='list_sessions', source=source['session_id'], project_id=source['project_id'])
    cursor = _decode(request['cursor'], expected)
    before, identity = None, None
    if cursor:
        before, identity = cursor.get('updated_at'), cursor.get('session_id')
        if not isinstance(before, str) or not isinstance(identity, str):
            _fail('history_cursor_stale')
    rows = storage._read('''SELECT s.session_id,s.title,s.updated_at,s.state,s.thread_id,s.conversation_revision,
        (SELECT COUNT(*) FROM naiyue_chat_sync h WHERE h.session_id=s.session_id) AS archive_threads
        FROM naiyue_sessions s WHERE s.project_id=? AND s.session_id!=?
        AND (? IS NULL OR s.updated_at<? OR (s.updated_at=? AND s.session_id>?))
        ORDER BY s.updated_at DESC,s.session_id ASC LIMIT ?''',
        (source['project_id'], source['session_id'], before, before, before, identity, count + 1))
    result = dict(status='ok', items=[], has_more=False, next_cursor=None)
    for i, row in enumerate(rows[:count]):
        item = {k: row[k] for k in ('session_id', 'title', 'updated_at', 'state')}
        summary=read_overview(storage.connection,row)
        if 'updated_at' in summary:summary['overview_updated_at']=summary.pop('updated_at')
        item.update(summary,state_source='saved',archive_available=bool(row['archive_threads']))
        old_cursor = result['next_cursor']
        result.update(has_more=i + 1 < len(rows), next_cursor=_encode({**expected,
            'updated_at': row['updated_at'], 'session_id': row['session_id']}) if i + 1 < len(rows) else None)
        if not _fit_record(result, item, settings.max_output_bytes):
            result.update(has_more=True, next_cursor=old_cursor)
            break
    return result


def _scope(storage, source, request):
    target = storage.get(request['session_id'])
    if target['project_id'] != source['project_id']:
        _fail('history_scope_mismatch')
    thread, saved_checkpoint = target['thread_id'], target['checkpoint_id']
    if request['agent_id'] is not None:
        child = storage.get_subagent(target['session_id'], request['agent_id'])
        if (not child or child['child_thread_id'] != 'subagent:' + request['agent_id']
                or child['agent_path'] != f"root/{child['agent']}/{request['agent_id']}"):
            _fail('history_child_not_found')
        thread, saved_checkpoint = child['child_thread_id'], child['checkpoint_id']
    head = next((h for h in storage.chat_heads(target['session_id']) if h['thread_id'] == thread and not h['checkpoint_ns']), None)
    upper = storage._read("""SELECT COALESCE(MAX(sequence),0) AS n FROM naiyue_chat_messages
        WHERE session_id=? AND thread_id=? AND checkpoint_ns=''""", (target['session_id'], thread))[0]['n']
    coverage = dict(session_id=target['session_id'], agent_id=request['agent_id'], thread_id=thread,
        conversation_revision=target['conversation_revision'], history_version=head['history_version'] if head else 0,
        upper=upper, checkpoint_id=head['checkpoint_id'] if head else None,
        status='saved' if head else 'unprepared' if saved_checkpoint else 'empty')
    return target, thread, coverage


def _messages(storage, source, request, settings, count):
    target, thread, coverage = _scope(storage, source, request)
    expected = dict(v=1, source=source['session_id'], project_id=source['project_id'], action=request['action'],
        target=target['session_id'], agent=request['agent_id'], thread=thread,
        query_hash=sha256((request['query'] or '').encode()).hexdigest(),
        history_version=coverage['history_version'], conversation_revision=coverage['conversation_revision'])
    cursor = _decode(request['cursor'], expected)
    before = coverage['upper'] + 1
    if cursor:
        upper, before = cursor.get('upper'), cursor.get('before')
        if (type(upper) is not int or type(before) is not int or upper < 0
                or upper > coverage['upper'] or not 0 < before <= upper + 1):
            _fail('history_cursor_stale')
        coverage['upper'] = upper
    result = dict(status='ok', items=[], has_more=False, next_cursor=None, coverage=coverage, scanned=0)
    if request['agent_id'] is None:
        summary=read_overview(storage.connection,target)
        if 'updated_at' in summary:summary['overview_updated_at']=summary.pop('updated_at')
        result.update(summary)
    if request['sequence'] is not None:
        if request['conversation_revision'] != coverage['conversation_revision']:
            _fail('history_cursor_stale')
        row = projection.message(storage.connection, target['session_id'], thread, request['sequence'],
                                 offset=request['offset'], chars=settings.read_chunk_chars)
        if row is None:
            _fail('history_message_not_found')
        if row['revision'] != request['revision']:
            _fail('history_cursor_stale')
        if request['offset'] > row['text_chars']:
            _fail('history_invalid_offset')
        item = projection.public_record(row, coverage['conversation_revision'])
        _fit_record(result, item, settings.max_output_bytes)
        result['has_more'] = not item['complete']
        # has_more is encoded too, including at the exact output budget boundary.
        if _size(result) > settings.max_output_bytes:
            item['content'] = item['content'][:-1]
            projection.finish_record(item)
        return result
    search = request['action'] == 'search'
    terms = tuple(dict.fromkeys(request['query'].split())) if search else ()
    scan = settings.search_batch_messages if search else count
    rows = projection.page(storage.connection, target['session_id'], thread, upper=coverage['upper'],
        before=before, count=scan + 1, chars=settings.preview_chars if search else settings.read_chunk_chars, terms=terms)
    for i, row in enumerate(rows[:scan]):
        old_cursor, old_scanned = result['next_cursor'], result['scanned']
        result.update(scanned=i + 1, has_more=i + 1 < len(rows), next_cursor=_encode({**expected,
            'upper': coverage['upper'], 'before': row['sequence']}) if i + 1 < len(rows) else None)
        if row['matched']:
            item = projection.public_record(row, coverage['conversation_revision'])
            if not _fit_record(result, item, settings.max_output_bytes):
                result.update(has_more=True, next_cursor=old_cursor, scanned=old_scanned)
                break
            if len(result['items']) == count:
                break
    if not search:
        result['items'].reverse()
    return result


def query(storage, source_session_id, project_id, request, settings):
    source = storage.get(source_session_id)
    binding = storage.get_workspace_binding()
    if (project_id is None or not binding or binding['workspace_id'] != project_id
            or source['project_id'] != project_id):
        _fail('history_scope_mismatch')
    count = request['limit'] if request['limit'] is not None else settings.default_limit
    if count > settings.max_limit or request['query'] is not None and len(request['query']) > settings.max_query_chars:
        _fail('history_invalid_limit')
    if request['action'] == 'list_sessions':
        result = _directory(storage, source, request, settings, count)
    else:
        result = _messages(storage, source, request, settings, count)
    if _size(result) > settings.max_output_bytes:
        _fail('history_output_budget')
    return result
