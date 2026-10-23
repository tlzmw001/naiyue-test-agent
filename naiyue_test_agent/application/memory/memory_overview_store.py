"""Transactional overview facts and trigger progress, separate from model work."""

from contextlib import contextmanager
import json
import logging
import sqlite3

from naiyue_test_agent.application.conversation import history_projection
from .memory_queries import MemoryReadError


logger = logging.getLogger(__name__)
REQUIRED = {
    'naiyue_memory_overviews': 'session_id thread_id conversation_revision version overview_json lower_sequence upper_sequence task_id origin_run_id batch skipped_json history_version updated_at',
    'naiyue_memory_progress': 'session_id thread_id conversation_revision completed_turns completed_chars accepted_turns accepted_chars next_sequence next_offset directory_seen last_reply_id',
}
FIELDS = ('goal', 'progress', 'blockers', 'next_step')


def initialize_project(connection):
    version = connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0]
    if version >= 17:return
    if version != 16:raise ValueError('memory_overview_schema_mismatch')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''CREATE TABLE naiyue_memory_overviews(
            session_id TEXT PRIMARY KEY,thread_id TEXT NOT NULL,conversation_revision INTEGER NOT NULL,
            version INTEGER NOT NULL CHECK(version>0),overview_json TEXT NOT NULL CHECK(json_valid(overview_json)),
            lower_sequence INTEGER NOT NULL,upper_sequence INTEGER NOT NULL,task_id TEXT NOT NULL,origin_run_id TEXT,
            batch INTEGER NOT NULL,skipped_json TEXT NOT NULL CHECK(json_valid(skipped_json)),
            history_version INTEGER NOT NULL,updated_at TEXT NOT NULL)''')
        connection.execute('''CREATE TABLE naiyue_memory_progress(
            session_id TEXT PRIMARY KEY,thread_id TEXT NOT NULL,conversation_revision INTEGER NOT NULL,
            completed_turns INTEGER NOT NULL CHECK(completed_turns>=0),
            completed_chars INTEGER NOT NULL CHECK(completed_chars>=0),
            accepted_turns INTEGER NOT NULL CHECK(accepted_turns>=0),
            accepted_chars INTEGER NOT NULL CHECK(accepted_chars>=0),
            next_sequence INTEGER NOT NULL CHECK(next_sequence>=0),next_offset INTEGER NOT NULL CHECK(next_offset>=0),
            directory_seen INTEGER NOT NULL CHECK(directory_seen IN (0,1)),last_reply_id TEXT)''')
        connection.execute('UPDATE naiyue_store_info SET version=17')


@contextmanager
def _transaction(storage, *, write=False):
    try:
        with storage._lock, storage.connection:
            storage.connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield storage.connection
    except sqlite3.Error as error:
        logger.error('memory_overview_storage_failed error_type=%s', type(error).__name__)
        raise MemoryReadError('memory_overview_storage_failed') from error


def _session(connection, session_id):
    row = connection.execute('''SELECT s.* FROM naiyue_sessions s JOIN naiyue_workspace w
        ON w.singleton=1 AND s.project_id=w.workspace_id WHERE s.session_id=?''', (session_id,)).fetchone()
    if row is None:raise MemoryReadError('memory_scope_mismatch')
    return dict(row)


def _position(connection, row):
    scope = (row['session_id'], row['thread_id'])
    upper = connection.execute('''SELECT COALESCE(MAX(sequence),0) FROM naiyue_chat_messages
        WHERE session_id=? AND thread_id=? AND checkpoint_ns='' ''', scope).fetchone()[0]
    head = connection.execute('''SELECT history_version FROM naiyue_chat_sync
        WHERE session_id=? AND thread_id=? AND checkpoint_ns='' ''', scope).fetchone()
    return dict(upper=upper,history_version=head['history_version'] if head else 0)


def _facts(connection, row):
    scope = (row['session_id'], row['thread_id'])
    chars = connection.execute(f'''SELECT COALESCE(SUM(naiyue_visible_length({history_projection.VISIBLE_TEXT})),0)
        FROM naiyue_chat_messages m WHERE {history_projection.VISIBLE_WHERE}
        AND m.message_type IN ('human','ai')
        AND COALESCE(json_extract(m.body_json,'$.data.additional_kwargs.naiyue_compaction_summary'),0)!=1''', scope).fetchone()[0]
    human = connection.execute(f'''SELECT m.sequence,COALESCE(m.reply_id,m.message_id) AS reply_id
        FROM naiyue_chat_messages m WHERE {history_projection.VISIBLE_WHERE} AND m.message_type='human'
        AND COALESCE(json_extract(m.body_json,'$.data.additional_kwargs.naiyue_input_kind'),'ordinary')
            NOT IN ('steering','delegated_task')
        AND COALESCE(json_extract(m.body_json,'$.data.additional_kwargs.naiyue_input_source'),'')!='delegated_task'
        AND COALESCE(json_extract(m.body_json,'$.data.additional_kwargs.naiyue_compaction_summary'),0)!=1
        ORDER BY m.sequence DESC LIMIT 1''', scope).fetchone()
    return _position(connection,row) | dict(chars=chars,
        last_reply_id=human['reply_id'] if human else None, last_sequence=human['sequence'] if human else 0)


def _progress(connection, session, facts):
    saved = connection.execute('SELECT * FROM naiyue_memory_progress WHERE session_id=?', (session['session_id'],)).fetchone()
    if saved and (saved['thread_id'],saved['conversation_revision']) == (session['thread_id'],session['conversation_revision']):
        return dict(saved) | {'directory_seen':bool(saved['directory_seen'])}
    row = dict(session_id=session['session_id'],thread_id=session['thread_id'],conversation_revision=session['conversation_revision'],
        completed_turns=0,completed_chars=facts['chars'],accepted_turns=0,accepted_chars=facts['chars'],
        next_sequence=facts['last_sequence'],next_offset=0,directory_seen=bool(saved['directory_seen']) if saved else False,
        last_reply_id=facts['last_reply_id'])
    connection.execute('''INSERT INTO naiyue_memory_progress
        (session_id,thread_id,conversation_revision,completed_turns,completed_chars,accepted_turns,accepted_chars,
        next_sequence,next_offset,directory_seen,last_reply_id)
        VALUES(:session_id,:thread_id,:conversation_revision,:completed_turns,:completed_chars,:accepted_turns,
        :accepted_chars,:next_sequence,:next_offset,:directory_seen,:last_reply_id)
        ON CONFLICT(session_id) DO UPDATE SET thread_id=excluded.thread_id,conversation_revision=excluded.conversation_revision,
        completed_turns=excluded.completed_turns,completed_chars=excluded.completed_chars,accepted_turns=excluded.accepted_turns,
        accepted_chars=excluded.accepted_chars,next_sequence=excluded.next_sequence,next_offset=excluded.next_offset,
        directory_seen=excluded.directory_seen,last_reply_id=excluded.last_reply_id''', row)
    return row


def ensure(storage, session_id):
    with _transaction(storage,write=True) as connection:
        session = _session(connection,session_id)
        return _progress(connection,session,_facts(connection,session))


def tick(storage, session_id):
    with _transaction(storage,write=True) as connection:
        session = _session(connection,session_id)
        facts = _facts(connection,session)
        row = _progress(connection,session,facts)
        newer = facts['last_reply_id'] is not None and facts['last_reply_id'] != row['last_reply_id']
        row.update(completed_turns=row['completed_turns']+int(newer),completed_chars=facts['chars'],last_reply_id=facts['last_reply_id'])
        connection.execute('''UPDATE naiyue_memory_progress SET completed_turns=:completed_turns,
            completed_chars=:completed_chars,last_reply_id=:last_reply_id WHERE session_id=:session_id''', row)
        return row


def accept(storage, session_id, *, thread_id, conversation_revision, upper):
    with _transaction(storage,write=True) as connection:
        session = _session(connection,session_id)
        facts = _facts(connection,session)
        _check_branch(session,thread_id,conversation_revision)
        if type(upper) is not int or not 0 <= upper <= facts['upper']:raise MemoryReadError('memory_overview_source_changed')
        row = _progress(connection,session,facts)
        row.update(accepted_turns=row['completed_turns'],accepted_chars=row['completed_chars'],
            next_sequence=max(row['next_sequence'],upper+1),next_offset=0)
        connection.execute('''UPDATE naiyue_memory_progress SET accepted_turns=:accepted_turns,accepted_chars=:accepted_chars,
            next_sequence=:next_sequence,next_offset=:next_offset WHERE session_id=:session_id''', row)
        return row


def mark_directory(storage, session_id):
    with _transaction(storage,write=True) as connection:
        session = _session(connection,session_id)
        row = _progress(connection,session,_facts(connection,session))
        connection.execute('UPDATE naiyue_memory_progress SET directory_seen=1 WHERE session_id=?',(session_id,))
        return row | {'directory_seen':True}


def _check_branch(session, thread_id, conversation_revision):
    if (session['thread_id'],session['conversation_revision']) != (thread_id,conversation_revision):
        raise MemoryReadError('memory_overview_source_changed')


def read_overview(connection, session):
    """Read an authorized target within the caller's existing transaction."""
    saved = connection.execute('SELECT * FROM naiyue_memory_overviews WHERE session_id=?',(session['session_id'],)).fetchone()
    if saved is None:return dict(overview=None,overview_status='not_generated')
    facts = _position(connection,session)
    stale = (saved['thread_id'],saved['conversation_revision'],saved['history_version']) != (
        session['thread_id'],session['conversation_revision'],facts['history_version'])
    return dict(overview=json.loads(saved['overview_json']),overview_status='stale' if stale else 'generated',
        overview_version=saved['version'],updated_at=saved['updated_at'],coverage=dict(
            lower=saved['lower_sequence'],upper=saved['upper_sequence'],thread_id=saved['thread_id'],
            conversation_revision=saved['conversation_revision'],history_version=saved['history_version'],
            task_id=saved['task_id'],origin_run_id=saved['origin_run_id'],batch=saved['batch'],
            skipped=json.loads(saved['skipped_json']),has_newer_messages=facts['upper']>saved['upper_sequence']))


def overview(storage, session_id):
    with _transaction(storage) as connection:return read_overview(connection,_session(connection,session_id))


def save_overview(storage, *, session_id, thread_id, conversation_revision, overview, lower, upper,
                  task_id, origin_run_id, batch, skipped, expected_history_version=None):
    if (not isinstance(overview,dict) or set(overview)!=set(FIELDS)
            or any(not isinstance(value,str) for value in overview.values())
            or sum(len(value) for value in overview.values())>1200
            or type(lower) is not int or type(upper) is not int or not 0 <= lower <= upper or upper == 0
            or not isinstance(task_id,str) or not task_id.strip() or len(task_id)>128
            or origin_run_id is not None and (not isinstance(origin_run_id,str) or not origin_run_id or len(origin_run_id)>128)
            or type(batch) is not int or not 0 <= batch < 9223372036854775807):
        raise MemoryReadError('memory_overview_invalid')
    try:skipped_json = json.dumps(skipped,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    except (TypeError,ValueError):raise MemoryReadError('memory_overview_invalid') from None
    if not isinstance(skipped,(bool,int,list)) or len(skipped_json)>4096:raise MemoryReadError('memory_overview_invalid')
    with _transaction(storage,write=True) as connection:
        session = _session(connection,session_id)
        _check_branch(session,thread_id,conversation_revision)
        facts = _position(connection,session)
        if (upper>facts['upper'] or expected_history_version is not None
                and (type(expected_history_version) is not int or expected_history_version!=facts['history_version'])):
            raise MemoryReadError('memory_overview_source_changed')
        old = connection.execute('SELECT * FROM naiyue_memory_overviews WHERE session_id=?',(session_id,)).fetchone()
        same_branch = old and (old['thread_id'],old['conversation_revision']) == (thread_id,conversation_revision)
        if same_branch and (upper<old['upper_sequence'] or upper==old['upper_sequence']
                and (task_id!=old['task_id'] or batch<=old['batch'])):
            raise MemoryReadError('memory_overview_stale')
        values = dict(session_id=session_id,thread_id=thread_id,conversation_revision=conversation_revision,
            version=old['version']+1 if old else 1,overview_json=json.dumps(overview,ensure_ascii=False,separators=(',',':')),
            lower_sequence=lower,upper_sequence=upper,task_id=task_id,origin_run_id=origin_run_id,batch=batch,
            skipped_json=skipped_json,history_version=facts['history_version'],updated_at=storage._now())
        connection.execute('''INSERT INTO naiyue_memory_overviews
            (session_id,thread_id,conversation_revision,version,overview_json,lower_sequence,upper_sequence,task_id,
            origin_run_id,batch,skipped_json,history_version,updated_at)
            VALUES(:session_id,:thread_id,:conversation_revision,:version,:overview_json,:lower_sequence,:upper_sequence,
            :task_id,:origin_run_id,:batch,:skipped_json,:history_version,:updated_at)
            ON CONFLICT(session_id) DO UPDATE SET thread_id=excluded.thread_id,conversation_revision=excluded.conversation_revision,
            version=excluded.version,overview_json=excluded.overview_json,lower_sequence=excluded.lower_sequence,
            upper_sequence=excluded.upper_sequence,task_id=excluded.task_id,origin_run_id=excluded.origin_run_id,
            batch=excluded.batch,skipped_json=excluded.skipped_json,history_version=excluded.history_version,updated_at=excluded.updated_at''',values)
        return read_overview(connection,session)


def directory(storage, session_id, limit):
    if type(limit) is not int or not 0 <= limit <= 50:raise MemoryReadError('memory_overview_invalid_limit')
    with _transaction(storage) as connection:
        current = _session(connection,session_id)
        rows = connection.execute('''SELECT * FROM naiyue_sessions WHERE project_id=? AND session_id!=?
            ORDER BY updated_at DESC,session_id LIMIT ?''',(current['project_id'],session_id,limit)).fetchall()
        result = []
        for raw in rows:
            row = dict(raw)
            value = {key:row[key] for key in ('session_id','title','state','updated_at')}
            summary = read_overview(connection,row)
            if 'updated_at' in summary:summary['overview_updated_at']=summary.pop('updated_at')
            result.append(value | summary)
        return result


def purge(connection, session_id):
    connection.execute('DELETE FROM naiyue_memory_overviews WHERE session_id=?',(session_id,))
    connection.execute('DELETE FROM naiyue_memory_progress WHERE session_id=?',(session_id,))
