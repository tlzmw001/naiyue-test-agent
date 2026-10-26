"""Validate a persisted memory source against its visible archive projection."""

from naiyue_test_agent.storage.conversation_sql import history_projection


def source_reference(connection, workspace_id, row, *, detail):
    result = {key: row[key] for key in ('source_workspace_id', 'session_id', 'thread_id', 'message_id',
        'revision', 'conversation_revision', 'reply_id', 'tool_call_id', 'role', 'occurred_at')}
    result.update(available=False, reason='other_workspace')
    if row['source_workspace_id'] != workspace_id:
        return result
    message = connection.execute('''SELECT m.sequence,s.conversation_revision FROM naiyue_chat_messages m
        JOIN naiyue_sessions s ON s.session_id=m.session_id WHERE m.session_id=? AND m.thread_id=?
        AND m.checkpoint_ns='' AND m.message_id=?''',
        (row['session_id'], row['thread_id'], row['message_id'])).fetchone()
    if not message:
        result['reason'] = 'source_unavailable'
        return result
    visible = history_projection.message(connection, row['session_id'], row['thread_id'],
        message['sequence'], offset=row['start_offset'], chars=min(len(row['quote']), 300))
    if (visible is None or visible['revision'] != row['revision']
            or message['conversation_revision'] != row['conversation_revision']
            or visible['tool_call_id'] != row['tool_call_id'] or visible['reply_id'] != row['reply_id']
            or visible['message_type'] != row['role'] or row['start_offset'] < 0
            or row['end_offset'] - row['start_offset'] != len(row['quote'])
            or len(row['quote']) > 300 or visible['content'] != row['quote']):
        result['reason'] = 'source_changed_or_not_visible'
        return result
    result.update(available=True, reason=None)
    if detail:
        result.update(quote=row['quote'], start_offset=row['start_offset'], end_offset=row['end_offset'])
    return result
