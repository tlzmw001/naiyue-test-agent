"""Overview and threshold state advance from real saved root archive facts."""

import hashlib
import json
import sqlite3
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, message_to_dict
import pytest

from naiyue_test_agent.application.chat_history import ChatUpdate
from naiyue_test_agent.application.memory_queries import MemoryReadError
from naiyue_test_agent.application.session_storage import SessionStorage


@pytest.fixture
def store(tmp_path):
    from naiyue_test_agent.application.memory_overview_store import initialize_project
    with SessionStorage(tmp_path/'sessions.sqlite') as storage:
        storage.bind_workspace(str(uuid4()), tmp_path)
        storage.create('s', dict(thread_id='s', model_id='A', workspace_root=tmp_path,
            active_tool_names=['memory'], permission_mode='Default'))
        initialize_project(storage.connection)
        yield storage


def archive(storage, messages, *, sid='s', thread=None):
    thread = thread or sid
    heads = [row for row in storage.chat_heads(sid) if row['thread_id'] == thread]
    revision = heads[0]['revision'] if heads else 0
    rows = []
    for message in messages:
        body = json.dumps(message_to_dict(message), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        rows.append(dict(message_id=message.id, body_json=body, body_hash=hashlib.sha256(body.encode()).hexdigest(),
            kind='context_summary' if message.additional_kwargs.get('naiyue_compaction_summary') else 'message',
            message_type=message.type, source_checkpoint_id=f'cp-{revision}',
            reply_id=message.additional_kwargs.get('naiyue_reply_id'), parent_message_id=None, occurred_at=1000000000.))
    storage.save_chat_updates(sid, [ChatUpdate(thread, '', 'root' if thread == sid else 'root/child', revision,
        f'cp-{revision}', tuple(rows))])


def human(number, text='提问', **extra):
    return HumanMessage(id=f'h{number}', content=text, additional_kwargs={'naiyue_human_input':True,
        'naiyue_reply_id':f'reply-{number}', 'naiyue_input_kind':'ordinary', **extra})


def overview_save(storage, **changes):
    from naiyue_test_agent.application.memory_overview_store import save_overview
    arguments = dict(session_id='s', thread_id='s', conversation_revision=0,
        overview=dict(goal='排查配置', progress='已看源码', blockers='', next_step='验证接口'),
        lower=1, upper=2, task_id='task-1', origin_run_id='run-1', batch=1, skipped=[])
    return save_overview(storage, **(arguments | changes))


def test_migration_is_complete_and_idempotent(store):
    from naiyue_test_agent.application.memory_overview_store import REQUIRED, initialize_project
    assert store.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 17
    for name, fields in REQUIRED.items():
        assert set(fields.split()) <= {row[1] for row in store.connection.execute(f'PRAGMA table_info({name})')}
    before = store.connection.total_changes
    initialize_project(store.connection)
    assert store.connection.total_changes == before


def test_migration_rolls_back_tables_and_version_when_ddl_fails(store):
    from naiyue_test_agent.application.memory_overview_store import REQUIRED, initialize_project
    with store.connection:
        for name in REQUIRED:store.connection.execute(f'DROP TABLE {name}')
        store.connection.execute('UPDATE naiyue_store_info SET version=16')
    store.connection.set_authorizer(lambda action, name, *unused: sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_CREATE_TABLE and name == 'naiyue_memory_progress' else sqlite3.SQLITE_OK)
    try:
        with pytest.raises(sqlite3.DatabaseError):initialize_project(store.connection)
    finally:store.connection.set_authorizer(None)
    assert store.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 16
    assert not store.connection.execute("SELECT 1 FROM sqlite_master WHERE name='naiyue_memory_overviews'").fetchall()


def test_new_progress_and_old_archive_baseline_use_visible_root_text_only(store):
    from naiyue_test_agent.application.memory_overview_store import ensure
    first = ensure(store, 's')
    assert {key:first[key] for key in ('completed_turns','completed_chars','accepted_turns','accepted_chars',
        'next_sequence','next_offset','directory_seen')} == dict(completed_turns=0,completed_chars=0,
            accepted_turns=0,accepted_chars=0,next_sequence=0,next_offset=0,directory_seen=False)
    with store.connection:store.connection.execute('DELETE FROM naiyue_memory_progress WHERE session_id=?', ('s',))
    archive(store, [human(1, '人\0文'), AIMessage(id='a1', content=[{'type':'text','text':'答复'},
        {'type':'reasoning','reasoning':'秘密思考'}, {'type':'image_url','image_url':{'url':'data:x'}}]),
        ToolMessage(id='tool', content='洪峰'*1000, tool_call_id='call'),
        HumanMessage(id='summary', content='摘要', additional_kwargs={'naiyue_compaction_summary':True}),
        SystemMessage(id='system', content='系统秘密')])
    archive(store, [human(2,'子任务')], thread='child')
    row = ensure(store, 's')
    assert row['completed_chars'] == row['accepted_chars'] == 5
    assert row['completed_turns'] == row['accepted_turns'] == 0
    assert row['last_reply_id'] == 'reply-1' and row['next_sequence'] == 1


def test_tick_counts_new_root_reply_once_and_accept_advances_only_on_admission(store):
    from naiyue_test_agent.application.memory_overview_store import ensure, tick, accept
    archive(store, [human(1), AIMessage(id='a1',content='完成')])
    ensure(store, 's')
    archive(store, [human(2), AIMessage(id='a2',content='继续完成')])
    first = tick(store, 's')
    second = tick(store, 's')
    assert first['completed_turns'] == second['completed_turns'] == 1
    assert second['accepted_turns'] == 0 and second['accepted_chars'] == 4
    archive(store, [AIMessage(id='a3',content='续跑结果')])
    third = tick(store, 's')
    assert third['completed_turns'] == 1 and third['completed_chars'] == 14
    accepted = accept(store, 's', thread_id='s', conversation_revision=0, upper=5)
    assert accepted['accepted_turns'] == 1 and accepted['accepted_chars'] == 14
    assert accepted['next_sequence'] == 6 and accepted['next_offset'] == 0
    with pytest.raises(MemoryReadError):accept(store, 's', thread_id='other', conversation_revision=0, upper=5)
    with pytest.raises(MemoryReadError):accept(store, 's', thread_id='s', conversation_revision=0, upper=6)


def test_branch_change_resets_baseline_but_keeps_directory_seen(store):
    from naiyue_test_agent.application.memory_overview_store import ensure, tick, mark_directory
    ensure(store, 's');mark_directory(store, 's')
    archive(store, [human(1), AIMessage(id='a1',content='完成')])
    assert tick(store, 's')['completed_turns'] == 1
    with store.connection:store.connection.execute('UPDATE naiyue_sessions SET thread_id=?,conversation_revision=1 WHERE session_id=?', ('branch','s'))
    archive(store, [human(2,'新分支')], thread='branch')
    row = ensure(store, 's')
    assert row['thread_id'] == 'branch' and row['conversation_revision'] == 1
    assert row['completed_turns'] == row['accepted_turns'] == 0
    assert row['completed_chars'] == row['accepted_chars'] == 3
    assert row['directory_seen'] is True


def test_steering_and_delegation_do_not_become_new_root_reply(store):
    from naiyue_test_agent.application.memory_overview_store import ensure, tick
    archive(store, [human(1)])
    ensure(store, 's')
    archive(store, [human(2,naiyue_input_kind='steering'),human(3,naiyue_input_kind='delegated_task')])
    row = tick(store, 's')
    assert row['completed_turns'] == 0 and row['last_reply_id'] == 'reply-1'


def test_accept_failure_rolls_back_progress_and_logs_only_error_type(store, caplog):
    from naiyue_test_agent.application.memory_overview_store import ensure, tick, accept
    original = ensure(store, 's')
    archive(store, [human(1,'PRIVATE_SOURCE'), AIMessage(id='a1',content='完成')])
    tick(store, 's')
    store.connection.set_authorizer(lambda action,name,*unused: sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_UPDATE and name == 'naiyue_memory_progress' else sqlite3.SQLITE_OK)
    try:
        with pytest.raises(MemoryReadError,match='memory_overview_storage_failed'):
            accept(store,'s',thread_id='s',conversation_revision=0,upper=2)
    finally:store.connection.set_authorizer(None)
    after = ensure(store,'s')
    assert after['accepted_turns'] == original['accepted_turns'] == 0
    assert after['next_sequence'] == original['next_sequence'] == 0
    assert 'memory_overview_storage_failed error_type=DatabaseError' in caplog.text
    assert 'PRIVATE_SOURCE' not in caplog.text


def test_overview_versions_coverage_staleness_and_old_completion_rejection(store):
    from naiyue_test_agent.application.memory_overview_store import overview
    assert overview(store, 's') == dict(overview=None, overview_status='not_generated')
    archive(store, [human(1), AIMessage(id='a1',content='完成')])
    first = overview_save(store)
    assert first['overview_version'] == 1 and first['overview_status'] == 'generated'
    assert first['coverage']['upper'] == 2 and first['coverage']['task_id'] == 'task-1'
    assert overview_save(store, batch=2)['overview_version'] == 2
    with pytest.raises(MemoryReadError):overview_save(store, task_id='older-task', batch=3)
    with pytest.raises(MemoryReadError):overview_save(store, batch=1)
    assert overview(store, 's')['overview_version'] == 2
    archive(store, [human(2), AIMessage(id='a2',content='新结果')])
    assert overview(store, 's')['overview_status'] == 'generated'
    assert overview(store, 's')['coverage']['has_newer_messages'] is True
    assert overview_save(store, task_id='task-2', upper=4)['overview_version'] == 3
    with pytest.raises(MemoryReadError):overview_save(store, upper=2, batch=3)
    with pytest.raises(MemoryReadError):overview_save(store, upper=5)
    assert overview(store, 's')['overview_version'] == 3


def test_overview_stale_on_archive_revision_or_branch_change(store):
    from naiyue_test_agent.application.memory_overview_store import overview
    archive(store, [human(1), AIMessage(id='a1',content='完成')]);overview_save(store)
    archive(store, [AIMessage(id='a1',content='已修正')])
    assert overview(store, 's')['overview_status'] == 'stale'
    with store.connection:store.connection.execute('UPDATE naiyue_sessions SET conversation_revision=1 WHERE session_id=?', ('s',))
    with pytest.raises(MemoryReadError):overview_save(store, upper=2,batch=2)


def test_read_overview_reuses_callers_existing_read_transaction(store):
    from naiyue_test_agent.application.memory_overview_store import read_overview
    archive(store,[human(1),AIMessage(id='a1',content='完成')]);overview_save(store)
    with store._lock,store.connection:
        store.connection.execute('BEGIN')
        value = read_overview(store.connection,store.get('s'))
        assert store.connection.in_transaction is True
        assert value['overview_status'] == 'generated' and value['overview_version'] == 1


def test_expected_history_version_is_checked_inside_commit_and_append_does_not_conflict(store):
    archive(store, [human(1), AIMessage(id='a1',content='完成')])
    assert overview_save(store, expected_history_version=0)['overview_version'] == 1
    archive(store, [human(2), AIMessage(id='a2',content='新增')])
    assert overview_save(store, batch=2, expected_history_version=0)['overview_version'] == 2
    archive(store, [AIMessage(id='a1',content='修正')])
    with pytest.raises(MemoryReadError):overview_save(store, batch=3, expected_history_version=0)


@pytest.mark.parametrize('changes', [dict(overview={'goal':'不完整'}),dict(overview=dict(goal='长'*1201,progress='',blockers='',next_step='')),
    dict(lower=3, upper=2),dict(task_id=''),dict(batch=-1)])
def test_invalid_overview_never_creates_authority(store, changes):
    from naiyue_test_agent.application.memory_overview_store import overview
    archive(store, [human(1), AIMessage(id='a1',content='完成')])
    with pytest.raises((MemoryReadError,ValueError)):overview_save(store, **changes)
    assert overview(store, 's')['overview_status'] == 'not_generated'


def test_directory_has_only_real_same_workspace_other_sessions_and_purge_is_scoped(store, tmp_path):
    from naiyue_test_agent.application.memory_overview_store import directory, ensure, purge
    archive(store, [human(1), AIMessage(id='a1',content='完成')]);overview_save(store);ensure(store,'s')
    assert directory(store,'s',8) == []
    store.create('other',dict(thread_id='other', model_id='A', workspace_root=tmp_path,
        active_tool_names=[], permission_mode='Default'))
    rows = directory(store,'s',8)
    assert len(rows) == 1 and rows[0]['session_id'] == 'other'
    assert rows[0]['overview'] is None and rows[0]['overview_status'] == 'not_generated'
    assert rows[0]['title'] is None and rows[0]['state'] == 'ready'
    with store.connection:
        purge(store.connection,'s')
    assert store.connection.execute('SELECT count(*) FROM naiyue_memory_overviews').fetchone()[0] == 0
    assert store.connection.execute('SELECT count(*) FROM naiyue_memory_progress').fetchone()[0] == 0
    assert len(store.chat_heads('s')) == 1
    with store.connection:store.connection.execute('UPDATE naiyue_sessions SET workspace_root=? WHERE session_id=?', ('foreign','other'))
    assert directory(store,'s',8) == []
    with pytest.raises(MemoryReadError):ensure(store,'other')
