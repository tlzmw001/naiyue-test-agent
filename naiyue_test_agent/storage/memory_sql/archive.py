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

    def metadata_page(self, session_id, thread_id, *, start, upper, limit):
        # Scan whole visible messages for secrets before returning any sliced text.
        sql = f"""SELECT {history_projection.FIELDS},m.occurred_at,
            COALESCE(json_extract(m.body_json,'$.data.additional_kwargs.naiyue_human_input'),0) AS user_origin,
            json_extract(m.body_json,'$.data.additional_kwargs.naiyue_input_kind') AS input_kind,
            naiyue_visible_length({history_projection.VISIBLE_TEXT}) AS text_chars,
            naiyue_memory_sensitive({history_projection.VISIBLE_TEXT}) AS sensitive
            FROM naiyue_chat_messages m WHERE {history_projection.VISIBLE_WHERE}
            AND m.sequence>=? AND m.sequence<=? ORDER BY m.sequence LIMIT ?"""
        rows = [dict(row) for row in self._connection.execute(sql,
            (session_id, thread_id, start, upper, limit + 1))]
        return dict(rows=rows[:limit], more=len(rows)>limit)

    def turn_bounds(self, session_id, thread_id, *, sequence, upper):
        row = self._connection.execute("""SELECT reply_id,input_sequence FROM naiyue_reply_boundaries
            WHERE session_id=? AND thread_id=? AND input_sequence<=?
            ORDER BY input_sequence DESC LIMIT 1""", (session_id, thread_id, sequence)).fetchone()
        if row is None:
            row = self._connection.execute('''SELECT message_id AS reply_id,sequence AS input_sequence
                FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND checkpoint_ns=''
                AND kind='message' AND message_type='human' AND sequence<=?
                AND json_extract(body_json,'$.data.additional_kwargs.naiyue_human_input')=1
                AND COALESCE(json_extract(body_json,'$.data.additional_kwargs.naiyue_input_kind'),'ordinary')
                    NOT IN ('steering','delegated_task') ORDER BY sequence DESC LIMIT 1''',
                (session_id,thread_id,sequence)).fetchone()
            if row is None:
                return dict(turn_key=f'unknown:{sequence}',start_sequence=sequence,end_sequence=sequence,
                    start_known=False,end_known=False)
            following = self._connection.execute('''SELECT MIN(sequence) FROM naiyue_chat_messages
                WHERE session_id=? AND thread_id=? AND checkpoint_ns='' AND kind='message'
                AND message_type='human' AND sequence>?
                AND json_extract(body_json,'$.data.additional_kwargs.naiyue_human_input')=1
                AND COALESCE(json_extract(body_json,'$.data.additional_kwargs.naiyue_input_kind'),'ordinary')
                    NOT IN ('steering','delegated_task')''',(session_id,thread_id,row['input_sequence'])).fetchone()[0]
            return dict(turn_key=row['reply_id'],start_sequence=row['input_sequence'],
                end_sequence=min(upper,following-1) if following else upper,
                start_known=True,end_known=following is not None and following<=upper+1)
        following = self._connection.execute("""SELECT MIN(input_sequence) FROM naiyue_reply_boundaries
            WHERE session_id=? AND thread_id=? AND input_sequence>?""",
            (session_id, thread_id, row['input_sequence'])).fetchone()[0]
        return dict(turn_key=row['reply_id'],start_sequence=row['input_sequence'],
            end_sequence=min(upper,following-1) if following else upper,
            start_known=True,end_known=following is not None and following<=upper+1)

    def tool_link(self, session_id, thread_id, *, parent_message_id, tool_call_id, before_sequence):
        if not parent_message_id or not tool_call_id:
            return None
        row = self._connection.execute("""SELECT m.sequence,m.reply_id,m.occurred_at,
            json_extract(t.value,'$.id') AS call_id,json_extract(t.value,'$.name') AS name
            FROM naiyue_chat_messages m,json_each(m.body_json,'$.data.tool_calls') t
            WHERE m.session_id=? AND m.thread_id=? AND m.checkpoint_ns='' AND m.kind='message'
            AND m.message_type='ai' AND m.message_id=? AND m.sequence<? AND json_extract(t.value,'$.id')=?""",
            (session_id,thread_id,parent_message_id,before_sequence,tool_call_id)).fetchall()
        if not row:return None
        if len(row)!=1:return dict(status='invalid')
        return dict(status='paired',parent_message_id=parent_message_id,parent_sequence=row[0]['sequence'],
            reply_id=row[0]['reply_id'],tool_call_id=tool_call_id,name=row[0]['name'],occurred_at=row[0]['occurred_at'])
