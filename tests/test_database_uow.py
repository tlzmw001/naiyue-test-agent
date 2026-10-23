"""Contract tests for the database owner and atomic cross-aggregate work."""

import sqlite3

import pytest

from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.application.errors import SessionPersistenceError


def create(store):
    store.sessions.create('s', dict(thread_id='t', model_id='m', workspace_root=None,
        active_tool_names=[], permission_mode='normal'))


def test_project_store_exposes_repositories_without_flat_legacy_api(tmp_path):
    with ProjectStore(tmp_path / 'sessions.sqlite') as store:
        create(store)
        assert store.sessions.get('s')['state'] == 'ready'
        assert isinstance(store.database, Database)
        assert not hasattr(store, 'get')
        assert not hasattr(store, 'connection')
        assert not hasattr(store.database, 'checkpoint_connection')
        assert all(getattr(store, name).database is store.database for name in
            ('sessions', 'conversation', 'replies', 'subagents', 'usage', 'diagnostics', 'memory'))


def test_nested_transaction_is_rejected_and_outer_writes_roll_back(tmp_path):
    with ProjectStore(tmp_path / 'sessions.sqlite') as store:
        create(store)
        with pytest.raises(RuntimeError, match='nested_database_transaction'):
            with store.database.transaction(write=True) as conn:
                conn.execute("UPDATE naiyue_sessions SET title='uncommitted' WHERE session_id='s'")
                with store.database.transaction(write=True):
                    pytest.fail('nested transaction entered')
        assert store.sessions.get('s')['title'] is None
        store.sessions.ensure_title('s', 'committed')
        assert store.sessions.get('s')['title'] == 'committed'


def test_finish_run_rolls_back_reply_and_chat_when_terminal_update_fails(tmp_path):
    import hashlib
    import json
    from langchain_core.messages import HumanMessage, message_to_dict
    from langgraph.checkpoint.sqlite import SqliteSaver
    from naiyue_test_agent.application.conversation.chat_history import ChatUpdate
    with ProjectStore(tmp_path / 'sessions.sqlite') as store:
        create(store)
        store.sessions.mark_running('s', 'r')
        SqliteSaver(store.database.connection).setup()
        child = dict(session_id='s', agent_id='c', agent_path='root/worker/c', child_thread_id='subagent:c',
            agent='worker', role_fingerprint='role', model_id='m', last_task='inspect', state='pending_parent',
            version=1, checkpoint_ns='', checkpoint_id='child-cp', last_delegation_id='delegation',
            last_parent_run_id='r', last_parent_batch_id='batch', last_parent_tool_call_id='call',
            last_parent_reply_id='reply', created_at='created', updated_at='updated', project_id=None,
            execution_root=None, execution_root_kind=None)
        store.subagents.save_subagent(child, None)
        with store.database.transaction(write=True) as conn:
            conn.execute("INSERT INTO checkpoints VALUES('subagent:c','','child-cp',NULL,'json',?,?)", (b'checkpoint', b'metadata'))
            conn.execute("INSERT INTO naiyue_regenerations VALUES('s','req','reply',0,'old','t','r','activated','[]')")
            conn.execute("""CREATE TRIGGER fail_terminal BEFORE UPDATE OF checkpoint_id ON naiyue_sessions
                BEGIN SELECT RAISE(ABORT,'injected'); END""")
        body = json.dumps(message_to_dict(HumanMessage(id='h', content='saved input')))
        message = dict(message_id='h', body_json=body, body_hash=hashlib.sha256(body.encode()).hexdigest(),
            kind='message', source_checkpoint_id='cp', message_type='human')
        boundary = dict(input_message_id='h', reply_id='reply', source_checkpoint_id='previous')
        updates = (ChatUpdate('t', '', 'root', 0, 'cp', (message,), boundaries=(boundary,)),
            ChatUpdate('subagent:c', '', 'root/worker/c', 0, 'child-cp', ()))
        with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
            store.runs.finish_run('s', 'ready', 'cp', '', 'completed', updates, subagent_run_id='r')
        assert store.conversation.chat_heads('s') == []
        assert store.database.read_rows('SELECT message_id FROM naiyue_chat_messages') == []
        assert store.replies.latest('s') is None
        assert store.subagents.get_subagent('s', 'c') == child
        assert store.replies.action('s', 'req')['status'] == 'activated'
        assert store.sessions.get('s')['state'] == 'running'
        with store.database.transaction(write=True) as conn:
            conn.execute('DROP TRIGGER fail_terminal')
        store.runs.finish_run('s', 'ready', 'cp', '', 'completed', updates, subagent_run_id='r')
        assert {row['checkpoint_id'] for row in store.conversation.chat_heads('s')} == {'cp', 'child-cp'}
        assert store.database.read_rows('SELECT message_id FROM naiyue_chat_messages') == [{'message_id': 'h'}]
        assert store.replies.latest('s')['reply_id'] == 'reply'
        assert store.subagents.get_subagent('s', 'c')['state'] == 'ready'
        assert store.subagents.get_subagent('s', 'c')['version'] == 2
        assert store.replies.action('s', 'req')['status'] == 'completed'
        assert store.sessions.get('s')['state'] == 'ready'


def test_parent_run_change_rolls_back_chat_head(tmp_path):
    from naiyue_test_agent.application.conversation.chat_history import ChatUpdate
    with ProjectStore(tmp_path / 'sessions.sqlite') as store:
        create(store)
        store.sessions.mark_running('s', 'new-run')
        update = ChatUpdate('t', '', 'root', 0, 'cp', ())
        with pytest.raises(SessionPersistenceError, match='subagent_parent_run_changed'):
            store.runs.finish_run('s', 'ready', 'cp', '', 'completed', (update,), subagent_run_id='old-run')
        assert store.conversation.chat_heads('s') == []
        assert store.sessions.get('s')['run_id'] == 'new-run'


@pytest.mark.parametrize('phase', ['begin', 'commit'])
def test_transaction_boundary_io_errors_keep_save_error_and_rollback(tmp_path, phase):
    with ProjectStore(tmp_path / 'sessions.sqlite') as store:
        create(store)
        connection = store.database.connection
        class FaultConnection:
            def __getattr__(self, name):
                return getattr(connection, name)
            def execute(self, sql, *args):
                if phase == 'begin' and sql == 'BEGIN IMMEDIATE':
                    raise sqlite3.OperationalError('PRIVATE_BOUNDARY_IO')
                return connection.execute(sql, *args)
            def commit(self):
                raise sqlite3.OperationalError('PRIVATE_BOUNDARY_IO')
        store.database.connection = FaultConnection()
        try:
            with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
                store.sessions.select_model('s', 'new-model')
        finally:
            store.database.connection = connection
        assert store.sessions.get('s')['model_id'] == 'm'
        assert not connection.in_transaction


@pytest.mark.parametrize('version,target', [(7, 8), (10, 11), (11, 12), (13, 14)])
def test_schema_step_ddl_failure_rolls_back_and_can_retry(tmp_path, version, target):
    from contextlib import closing
    from tests.storage_backup_fixture import closed_store
    from naiyue_test_agent.application.conversation import chat_queries, reply_storage
    from naiyue_test_agent.application.subagents import subagent_storage
    database, _, _ = closed_store(tmp_path, version=version)
    step = {7: chat_queries.migrate_chat_queries, 10: reply_storage.initialize,
        11: reply_storage.initialize_regeneration, 13: subagent_storage.initialize}[version]
    with closing(sqlite3.connect(database)) as conn:
        before = list(conn.execute('SELECT name,sql FROM sqlite_master ORDER BY name'))
        def deny_version_update(action, table, *args):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_UPDATE and table == 'naiyue_store_info' else sqlite3.SQLITE_OK
        conn.set_authorizer(deny_version_update)
        with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
            step(conn)
        conn.set_authorizer(None)
        assert list(conn.execute('SELECT name,sql FROM sqlite_master ORDER BY name')) == before
        assert conn.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == version
        step(conn)
        assert conn.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == target


def test_memory_batch_begin_failure_keeps_memory_error_code(tmp_path, caplog):
    from naiyue_test_agent.application.memory.memory_queries import MemoryReadError
    with ProjectStore(tmp_path / 'sessions.sqlite') as store:
        connection = store.database.connection
        class FaultConnection:
            def __getattr__(self, name):
                return getattr(connection, name)
            def execute(self, sql, *args):
                if sql == 'BEGIN':
                    raise sqlite3.OperationalError('PRIVATE_BATCH_IO')
                return connection.execute(sql, *args)
        store.database.connection = FaultConnection()
        try:
            with pytest.raises(MemoryReadError, match='memory_batch_read_failed'):
                store.memory.build_batch(session_id='s', thread_id='t', conversation_revision=0,
                    start=(0, 0), upper=0, token_budget=100, source_chars=100)
        finally:
            store.database.connection = connection
        assert 'memory_batch_read_failed' in caplog.text
        assert 'PRIVATE_BATCH_IO' not in caplog.text


def test_close_waits_until_active_transaction_releases_connection(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, get_ident
    with ProjectStore(tmp_path / 'sessions.sqlite') as store:
        lock, main_thread, attempted = store.database.lock, get_ident(), Event()
        class ObservedLock:
            def __enter__(self):
                if get_ident() != main_thread:
                    attempted.set()
                return lock.__enter__()
            def __exit__(self, *args):
                return lock.__exit__(*args)
        store.database.lock = ObservedLock()
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                with store.database.transaction(write=True) as connection:
                    closing = pool.submit(store.database.close)
                    assert attempted.wait(5), 'close did not acquire the database ownership lock'
                    assert not closing.done()
                    assert connection.execute('SELECT 1').fetchone()[0] == 1
            finally:
                closing.result(timeout=5)
        assert store.database._closed
