"""Bounded reads of archived facts through the owning repository transaction."""

from naiyue_test_agent.storage.queries import read_rows, subagent_references

from naiyue_test_agent.application.errors import SessionPersistenceError
from naiyue_test_agent.application.subagents.subagent_history import subagent_target


def migrate_chat_queries(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] >= 8:
        return
    with connection:
        connection.execute('BEGIN')
        connection.execute('ALTER TABLE naiyue_chat_sync ADD COLUMN history_version INTEGER NOT NULL DEFAULT 0')
        connection.execute('ALTER TABLE naiyue_chat_sync ADD COLUMN stopped INTEGER NOT NULL DEFAULT 0')
        connection.execute('ALTER TABLE naiyue_chat_messages ADD COLUMN message_type TEXT')
        connection.execute('ALTER TABLE naiyue_chat_messages ADD COLUMN parent_message_id TEXT')
        connection.execute("UPDATE naiyue_chat_messages SET message_type=json_extract(body_json,'$.data.type')")
        connection.execute('''UPDATE naiyue_chat_messages AS t SET parent_message_id=(
            SELECT a.message_id FROM naiyue_chat_messages a WHERE a.session_id=t.session_id
            AND a.thread_id=t.thread_id AND a.checkpoint_ns=t.checkpoint_ns AND a.sequence<t.sequence
            AND a.message_type='ai' AND EXISTS(SELECT 1 FROM json_each(a.body_json,'$.data.tool_calls') c
                WHERE json_extract(c.value,'$.id')=json_extract(t.body_json,'$.data.tool_call_id'))
            AND NOT EXISTS(SELECT 1 FROM naiyue_chat_messages m WHERE m.session_id=t.session_id
                AND m.thread_id=t.thread_id AND m.checkpoint_ns=t.checkpoint_ns
                AND m.sequence>a.sequence AND m.sequence<t.sequence AND m.message_type!='tool')
            ORDER BY a.sequence DESC LIMIT 1) WHERE t.message_type='tool' ''')
        connection.execute('UPDATE naiyue_store_info SET version=8')


def read_page(connection, session_id, thread_id, *, limit, body_bytes, preview_chars, before=None, upper=None, version=None):
    scope = (session_id, thread_id, '')
    heads = read_rows(connection, '''SELECT * FROM naiyue_chat_sync
        WHERE session_id=? AND thread_id=? AND checkpoint_ns=?''', scope)
    head = heads[0] if heads else None
    current = head['history_version'] if head else 0
    if version is not None and version != current:
        raise SessionPersistenceError('history_cursor_stale')
    maximum = read_rows(connection, '''SELECT COALESCE(MAX(sequence),0) AS n FROM naiyue_chat_messages
        WHERE session_id=? AND thread_id=? AND checkpoint_ns=? AND kind='message' ''', scope)[0]['n']
    upper = maximum if upper is None else upper
    if upper > maximum:
        raise SessionPersistenceError('history_cursor_stale')
    records = read_rows(connection, '''SELECT message_id,sequence,revision,message_type,parent_message_id,reply_id,
        CASE WHEN length(CAST(body_json AS BLOB))<=? THEN body_json END AS body_json,
        CASE WHEN json_type(body_json,'$.data.content')='text'
            THEN substr(json_extract(body_json,'$.data.content'),1,?) ELSE '' END AS preview,
        substr(COALESCE(json_extract(body_json,'$.data.additional_kwargs.reasoning_content'),
            (SELECT COALESCE(json_extract(c.value,'$.reasoning'),json_extract(c.value,'$.thinking'),
                json_extract(c.value,'$.summary[0].text')) FROM json_each(body_json,'$.data.content') c
                WHERE c.type='object' AND json_extract(c.value,'$.type') IN ('reasoning','thinking') LIMIT 1)),1,?) AS reasoning_preview,
        CASE WHEN json_extract(body_json,'$.data.artifact.type')='file_change'
            AND json_extract(body_json,'$.data.artifact.version')=1
            AND json_extract(body_json,'$.data.artifact.operation') IN ('write','edit')
            AND length(json_extract(body_json,'$.data.artifact.path'))<=4096
        THEN json_object('type','file_change','version',1,
            'operation',json_extract(body_json,'$.data.artifact.operation'),
            'path',json_extract(body_json,'$.data.artifact.path'),
            'firstChangedLine',json_extract(body_json,'$.data.artifact.firstChangedLine'),
            'unavailable_reason',substr(json_extract(body_json,'$.data.artifact.unavailable_reason'),1,80),
            'diff','','preview','','diff_truncated',json('true'),'preview_truncated',json('true'),
            'patch_omitted',json(CASE WHEN json_type(body_json,'$.data.artifact.patch')='text' THEN 'false' ELSE 'true' END))
        END AS file_change_json,
        CASE WHEN json_type(body_json,'$.data.artifact')='object'
            AND json_type(body_json,'$.data.artifact.source')='object'
            AND length(CAST(json_remove(json_extract(body_json,'$.data.artifact'),'$.structured_content') AS BLOB))<=4096
        THEN json_remove(json_extract(body_json,'$.data.artifact'),'$.structured_content') END AS mcp_artifact_json,
        json_extract(body_json,'$.data.tool_call_id') AS tool_call_id,
        json_extract(body_json,'$.data.name') AS name,json_extract(body_json,'$.data.status') AS status,
        kind FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND checkpoint_ns=? AND kind='message'
        AND sequence<=? AND sequence<? ORDER BY sequence DESC LIMIT ?''',
        (body_bytes, preview_chars, preview_chars, *scope, upper, before if before is not None else upper + 1, limit + 1))
    has_more = len(records) > limit
    records = records[:limit]
    for record in records:
        if record['message_type'] == 'ai' and record['body_json'] is None:
            calls = read_rows(connection, '''SELECT json_extract(c.value,'$.id') AS id,
                json_extract(c.value,'$.name') AS name FROM naiyue_chat_messages m,
                json_each(m.body_json,'$.data.tool_calls') c WHERE m.session_id=? AND m.thread_id=?
                AND m.checkpoint_ns=? AND m.sequence=? LIMIT ?''', (*scope, record['sequence'], limit + 1))
            record['calls'], record['tool_calls_truncated'] = calls[:limit], len(calls) > limit
    return {'records': records, 'has_more': has_more, 'upper': upper, 'history_version': current,
            'checkpoint_id': head['checkpoint_id'] if head else None, 'sync_revision': head['revision'] if head else 0,
            'has_compaction': bool(read_rows(connection, '''SELECT 1 FROM naiyue_chat_messages
                WHERE session_id=? AND thread_id=? AND checkpoint_ns=? AND kind='context_summary' LIMIT 1''', scope))}


def read_message(connection, session_id, thread_id, sequence, *, revision, offset, chars, conversation_revision=0):
    records = read_rows(connection, '''SELECT revision,length(body_json) AS characters,substr(body_json,?,?) AS body_json_chunk,
        (SELECT conversation_revision FROM naiyue_sessions WHERE session_id=?) AS conversation_revision
        FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND checkpoint_ns='' AND sequence=?''',
        (offset + 1, chars, session_id, session_id, thread_id, sequence))
    if not records:
        raise SessionPersistenceError('history_message_not_found')
    record = records[0]
    if record.pop('conversation_revision') != conversation_revision or record['revision'] != revision:
        raise SessionPersistenceError('history_cursor_stale')
    if offset > record['characters']:
        raise SessionPersistenceError('history_invalid_offset')
    next_offset = offset + len(record['body_json_chunk'])
    return {**record, 'next_offset': next_offset, 'complete': next_offset >= record['characters']}


def approval_seed(connection, session_id, thread_id, batch_id):
    """Read only the trusted pending batch's call identities, never its arguments/body."""
    calls = read_rows(connection, '''SELECT json_extract(c.value,'$.id') AS id,
        json_extract(c.value,'$.name') AS name FROM naiyue_chat_messages m,
        json_each(m.body_json,'$.data.tool_calls') c WHERE m.session_id=? AND m.thread_id=?
        AND m.checkpoint_ns='' AND m.message_id=? AND m.message_type='ai' ''',
        (session_id, thread_id, batch_id))
    if not calls:
        raise SessionPersistenceError('chat_history_source_missing')
    return [dict(id=batch_id, type='ai', content='', provisional=False, state='complete', identity_only=True,
                 tool_calls=[{**call, 'args': {}} for call in calls], arguments_truncated=True)]


def subagent_records(connection, session_id, root_thread):
    calls = read_rows(connection, '''SELECT m.message_id,json_extract(c.value,'$.id') AS call_id,
        json_extract(c.value,'$.args.agent') AS agent,json_extract(c.value,'$.args.task') AS task,
        json_extract(c.value,'$.args.agent_id') AS target_id
        FROM naiyue_chat_messages m,json_each(m.body_json,'$.data.tool_calls') c
        WHERE m.session_id=? AND m.thread_id=? AND m.message_type='ai'
        AND json_extract(c.value,'$.name')='subagent' ORDER BY m.sequence''', (session_id, root_thread))
    records = {}
    owned = subagent_references(connection, session_id)
    for call in calls:
        if not all(isinstance(call[k], str) for k in ('call_id', 'agent', 'task')):
            continue
        identity = subagent_target(session_id, call['message_id'], call['call_id'], call['target_id'],
            agent=call['agent'], owned_instances=owned)
        if identity is None:
            continue
        results = read_rows(connection, '''SELECT json_extract(body_json,'$.data.status') AS status FROM naiyue_chat_messages
            WHERE session_id=? AND thread_id=? AND parent_message_id=?
            AND json_extract(body_json,'$.data.tool_call_id')=? ORDER BY sequence DESC LIMIT 1''',
            (session_id, root_thread, call['message_id'], call['call_id']))
        heads = read_rows(connection, 'SELECT stopped FROM naiyue_chat_sync WHERE session_id=? AND thread_id=?',
                              (session_id, 'subagent:' + identity))
        status = 'unconfirmed' if not results else 'failed' if results[0]['status'] == 'error' else 'completed'
        if heads and heads[0]['stopped']:
            status = 'stopped'
        records[identity] = dict(agent_id=identity, agent_path=f'root/{call["agent"]}/{identity}', agent=call['agent'],
            task=call['task'], tool_call_id=call['call_id'], parent_batch_id=call['message_id'], status=status)
    return list(records.values())
