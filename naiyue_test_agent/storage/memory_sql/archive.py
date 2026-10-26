"""Saved source reads bound to an already-owned transaction."""

from naiyue_test_agent.storage.conversation_sql import history_projection


class ArchiveReader:
    def __init__(self, connection):
        self._connection = connection

    def parent(self, session_id):
        row = self._connection.execute('SELECT thread_id,conversation_revision,project_id FROM naiyue_sessions WHERE session_id=?',
            (session_id,)).fetchone()
        return dict(row) if row is not None else None

    def binding(self):
        from naiyue_test_agent.storage.queries import workspace_binding
        row=workspace_binding(self._connection)
        return row['workspace_id'] if row is not None else None

    def child(self, session_id, thread_id):
        row = self._connection.execute('SELECT agent_id,agent,agent_path FROM naiyue_subagent_instances WHERE session_id=? AND child_thread_id=?',
            (session_id, thread_id)).fetchone()
        return dict(row) if row is not None else None

    def saved(self, session_id, thread_id, message_id, *, current_user):
        if current_user:
            row = self._connection.execute('''SELECT * FROM naiyue_chat_messages WHERE session_id=? AND thread_id=?
                AND checkpoint_ns='' AND kind='message' AND message_type='human'
                AND json_extract(body_json,'$.data.additional_kwargs.naiyue_human_input')=1
                ORDER BY sequence DESC LIMIT 1''', (session_id, thread_id)).fetchone()
        else:
            row = self._connection.execute('''SELECT * FROM naiyue_chat_messages WHERE session_id=? AND thread_id=?
                AND checkpoint_ns='' AND message_id=?''', (session_id, thread_id, message_id)).fetchone()
        return dict(row) if row is not None else None

    def visible(self, session_id, thread_id, sequence, *, offset, chars):
        return history_projection.message(self._connection, session_id, thread_id, sequence, offset=offset, chars=chars)

    def history_version(self, session_id, thread_id):
        row = self._connection.execute("SELECT history_version FROM naiyue_chat_sync WHERE session_id=? AND thread_id=? AND checkpoint_ns=''",
            (session_id, thread_id)).fetchone()
        return row['history_version'] if row is not None else 0

    def next_visible(self, session_id, thread_id, start, upper):
        # Full visible text is checked before slicing; hidden JSON never leaves this query.
        sql = f"""SELECT {history_projection.FIELDS},m.occurred_at,
            naiyue_visible_length({history_projection.VISIBLE_TEXT}) AS text_chars,
            naiyue_memory_sensitive({history_projection.VISIBLE_TEXT}) AS sensitive
            FROM naiyue_chat_messages m WHERE {history_projection.VISIBLE_WHERE}
            AND m.sequence>=? AND m.sequence<=? ORDER BY m.sequence LIMIT 1"""
        row = self._connection.execute(sql, (session_id, thread_id, start, upper)).fetchone()
        return dict(row) if row is not None else None
