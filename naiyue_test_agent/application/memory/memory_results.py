"""Bounded public records; source quotes require the archive's visible projection."""

import json

from naiyue_test_agent.contracts.memory import MemoryReadError, CONDITIONS


def record(row, request, *, settings, source_rows, sources_truncated, storage, workspace_id):
    result = {key: row[key] for key in ('memory_id', 'version', 'scope', 'kind', 'status', 'title', 'basis',
        'created_at', 'updated_at', 'evidence_at', 'last_confirmed_at', 'ttl_days', 'expires_at')}
    result['conditions'] = {key: row[key] for key in CONDITIONS}
    result['needs_verification'] = any(row[key] is not None and request[key] is None for key in CONDITIONS)
    result['aliases'], result['tags'] = json.loads(row['aliases_json']), json.loads(row['tags_json'])
    detail = request['action'] == 'read'
    if detail:
        result.update(gaps=json.loads(row['gaps_json']),reason=row['reason'],deadline_at=row['deadline_at'])
    offset = request['offset'] if detail else 0
    chars = settings.read_chunk_chars if detail else settings.preview_chars
    if offset > len(row['body']):
        raise MemoryReadError('memory_invalid_offset')
    result.update(body=row['body'][offset:offset + chars], offset=offset, body_chars=len(row['body']),
        sources=[storage.conversation.source_reference(workspace_id, source, detail=detail) for source in source_rows],
        sources_truncated=sources_truncated)
    for key in ('duplicate_ids', 'literal_match', 'match_coverage'):
        if key in row:result[key] = row[key]
    finish(result)
    return result


def finish(item):
    item['next_offset'] = item['offset'] + len(item['body'])
    item['complete'] = item['next_offset'] >= item['body_chars']


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


def fit(result, item, budget):
    result['items'].append(item)
    if size(result) <= budget:
        return True
    if len(result['items']) > 1:
        result['items'].pop()
        return False
    while item.get('history') and size(result) > budget:
        item['history'].pop()
        item['history_truncated'] = True
    while item['sources'] and size(result) > budget:
        item['sources'].pop()
        item['sources_truncated'] = True
    while len(item['body']) > 1 and size(result) > budget:
        item['body'] = item['body'][:len(item['body']) // 2]
        finish(item)
    if size(result) > budget:
        raise MemoryReadError('memory_output_budget')
    return True


def fit_mutation(result, budget):
    """Return a bounded success receipt inside the transaction, with detail by ID."""
    result['truncated'] = False
    item = result['item']
    while size(result) > budget:
        fields = [(len(item[key]),key) for key in ('body','title','reason') if len(item[key])>1]
        fields.extend((len(value),('gaps',index)) for index,value in enumerate(item['gaps']) if len(value)>1)
        if not fields:raise MemoryReadError('memory_output_budget')
        _,key=max(fields,key=lambda field:field[0])
        if isinstance(key,tuple):
            index=key[1];item['gaps'][index]=item['gaps'][index][:len(item['gaps'][index])//2]
        else:item[key]=item[key][:len(item[key])//2]
        result['truncated'] = True
    return result


def check_metadata(row, settings):
    """A new/current update must remain readable without sources or history."""
    detail=record(row,dict(action='read',offset=len(row['body']),**dict.fromkeys(CONDITIONS)),
        settings=settings,source_rows=[],sources_truncated=True,storage=None,workspace_id=None)
    envelope=dict(status='ok',items=[detail],requested_limit=50,truncated=False,has_more=False,next_cursor=None)
    # Reserve small headroom for one Unicode body character and chunk offsets/flags.
    if size(envelope)+64 > settings.max_output_bytes:
        raise MemoryReadError('memory_metadata_budget')
