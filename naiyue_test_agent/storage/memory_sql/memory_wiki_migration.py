"""One-store format4 -> Wiki isolation. No startup registration until S6."""
from . import memory_schema, memory_wiki_schema


def upgrade(c,*,scope,workspace_id,now):
    memory_schema.check(c,version=4)
    if scope=='workspace':
        if c.execute('SELECT version FROM naiyue_store_info').fetchone()[0]!=25:raise ValueError('memory_invalid_format')
        from naiyue_test_agent.storage.queries import workspace_binding
        binding=workspace_binding(c)
        if binding is None or binding['workspace_id']!=workspace_id:raise ValueError('memory_scope_mismatch')
    else:
        if c.execute('PRAGMA user_version').fetchone()[0]!=4:raise ValueError('memory_invalid_format')
    for name in ('insert','update','delete'):c.execute('DROP TRIGGER naiyue_memory_'+name)
    c.execute('DROP TABLE naiyue_memory_search')
    for old,new in (
        ('naiyue_memory_info','legacy143bm_info'),('naiyue_memories','legacy143bm_memories'),
        ('naiyue_memory_sources','legacy143bm_sources'),('naiyue_memory_versions','legacy143bm_versions'),
        ('naiyue_memory_suppression','legacy143bm_suppression'),
    ):c.execute(f'ALTER TABLE {old} RENAME TO {new}')
    memory_wiki_schema.create(c,scope=scope,workspace_id=workspace_id,now=now)
    c.execute('INSERT INTO naiyue_memory_suppression SELECT source_hash,forgotten_at FROM legacy143bm_suppression')
    if scope=='workspace':
        _baseline(c,workspace_id)
        c.execute('UPDATE naiyue_store_info SET version=26')
    else:c.execute('PRAGMA user_version=5')
    memory_wiki_schema.check(c)


def _baseline(c,workspace_id):
    from . import memory_overview_store as overview
    from naiyue_test_agent.storage.conversation_sql import history_projection as projection
    sessions=c.execute('SELECT * FROM naiyue_sessions WHERE project_id=?',(workspace_id,)).fetchall()
    for session in sessions:
        facts=overview._facts(c,session)
        row=overview._progress(c,session,facts)
        turns=c.execute(f'''SELECT COUNT(DISTINCT COALESCE(m.reply_id,m.message_id)) FROM naiyue_chat_messages m
            WHERE {projection.VISIBLE_WHERE} AND m.message_type='human'
            AND COALESCE(json_extract(m.body_json,'$.data.additional_kwargs.naiyue_input_kind'),'ordinary')
                NOT IN ('steering','delegated_task')
            AND COALESCE(json_extract(m.body_json,'$.data.additional_kwargs.naiyue_input_source'),'')!='delegated_task'
            AND COALESCE(json_extract(m.body_json,'$.data.additional_kwargs.naiyue_compaction_summary'),0)!=1''',
            (session['session_id'],session['thread_id'])).fetchone()[0]
        c.execute('''UPDATE naiyue_memory_progress SET completed_turns=?,accepted_turns=?,completed_chars=?,
            accepted_chars=?,next_sequence=?,next_offset=0,last_reply_id=? WHERE session_id=?''',
            (turns,turns,facts['chars'],facts['chars'],facts['upper']+1,facts['last_reply_id'],row['session_id']))
