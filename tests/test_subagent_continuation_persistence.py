"""Required child registration and the parent archive commit share authority."""

import json
import sqlite3

import pytest

from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent import application
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.storage.backup import list_backups, validate_backup
from tests.storage_backup_fixture import closed_store
from test_async_application import StreamingToolModel, call
from test_subagent_continuation import feedback
from test_subagent_execution import ChildModel
from test_subagent_restart import options


@async_test
async def test_sqlite_continuation_confirms_same_child_head_in_parent_transaction(monkeypatch, tmp_path):
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'first'}, 'first'), AIMessage(content='done')])
    child = ChildModel(replies=[AIMessage(content='one'), AIMessage(content='two')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    async with open_component_application('unused', **options(tmp_path)) as app:
        sid = await app.host.sessions.create('A')
        identity = json.loads(feedback(await app.host.runs.start(sid, [('user', 'start')])).content)['agent_id']
        first = app.host.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        assert first['state'] == 'ready' and first['checkpoint_id']
        root.replies.extend([call('subagent', {'agent': 'worker', 'task': 'second', 'agent_id': identity}, 'second'), AIMessage(content='done')])
        assert json.loads(feedback(await app.host.runs.start(sid, [('user', 'next')])).content)['report'] == 'two'
        latest = app.host.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        assert latest['child_thread_id'] == first['child_thread_id']
        assert latest['state'] == 'ready' and latest['version'] > first['version']
        assert latest['last_delegation_id'] != first['last_delegation_id']
        head = next(row for row in app.host.sessions._persistence.storage.conversation.chat_heads(sid) if row['thread_id'] == latest['child_thread_id'])
        assert head['checkpoint_id'] == latest['checkpoint_id'] != first['checkpoint_id']
        messages = await app.host.history.page(sid, agent_id=identity, limit=20, body_bytes=65536, preview_chars=160)
        assert json.loads(messages['records'][0]['body_json'])['data']['content'] == 'two'
        assert len(child.seen) == 2


@async_test
async def test_parent_archive_failure_cannot_mark_child_ready(monkeypatch, tmp_path, caplog):
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'first'}, 'first'), AIMessage(content='done')])
    child = ChildModel(replies=[AIMessage(content='child succeeded')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    async with open_component_application('unused', **options(tmp_path)) as app:
        sid = await app.host.sessions.create('A')
        from naiyue_test_agent.storage import unit_of_work
        original = unit_of_work.write_chat_updates
        def fail_archive(*args, **kwargs):
            raise sqlite3.OperationalError('PRIVATE_ARCHIVE_FAILURE')
        monkeypatch.setattr(unit_of_work, 'write_chat_updates', fail_archive)
        with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
            await app.host.runs.start(sid, [('user', 'start')])
        row = app.host.sessions._persistence.storage.database.connection.execute('SELECT * FROM naiyue_subagent_instances WHERE session_id=?', (sid,)).fetchone()
        assert row['state'] == 'pending_parent' and not app.host.sessions._persistence.storage.conversation.chat_heads(sid)
        assert app.host.sessions._sessions.require(sid).is_busy() is False
        assert 'PRIVATE_ARCHIVE_FAILURE' not in caplog.text
        monkeypatch.setattr(unit_of_work, 'write_chat_updates', original)


def test_schema13_migration_backs_up_once_and_leaves_legacy_instances_unregistered(tmp_path):
    database, _, _ = closed_store(tmp_path, version=13)
    with ProjectStore(database) as storage:
        assert storage.database.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == CURRENT_SCHEMA
        assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_subagent_instances').fetchone()[0] == 0
    backups = list_backups(database)
    assert len(backups) == 1
    manifest = validate_backup(database, backups[0]['manifest']['backup_id'])
    assert (manifest['source_schema'], manifest['target_schema']) == (13, CURRENT_SCHEMA)
    with ProjectStore(database):
        pass
    assert len(list_backups(database)) == 1


def test_schema14_migration_failure_rolls_back_and_retains_usable_backup(monkeypatch, tmp_path):
    from naiyue_test_agent.storage import subagent_sql as subagent_storage
    database, _, _ = closed_store(tmp_path, version=13)
    error = RuntimeError('PRIVATE_SCHEMA_FAILURE')
    def fail(connection):
        with connection:
            connection.execute('BEGIN')
            connection.execute('CREATE TABLE must_rollback(value TEXT)')
            raise error
    monkeypatch.setattr(subagent_storage, 'initialize', fail)
    with pytest.raises(SessionPersistenceError, match='migration_failed') as caught:
        ProjectStore(database)
    assert caught.value.__cause__ is error
    assert (caught.value.source_schema, caught.value.current_schema, caught.value.target_schema, caught.value.phase) == (13, 13, CURRENT_SCHEMA, 'schema_14')
    assert validate_backup(database, caught.value.backup_id)['source_schema'] == 13
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='must_rollback'").fetchone() is None


@pytest.mark.parametrize('point', ['claim', 'settle'])
@async_test
async def test_registration_io_failure_is_required_and_never_reports_ready(monkeypatch, tmp_path, caplog, point):
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'work'}, 'first'), AIMessage(content='must not run')])
    child = ChildModel(replies=[AIMessage(content='child report')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    async with open_component_application('unused', **options(tmp_path)) as app:
        sid = await app.host.sessions.create('A')
        sql = ('''CREATE TRIGGER registration_failure BEFORE INSERT ON naiyue_subagent_instances
            BEGIN SELECT RAISE(ABORT,'PRIVATE_REGISTRATION_FAILURE'); END''' if point == 'claim' else
            '''CREATE TRIGGER registration_failure BEFORE UPDATE ON naiyue_subagent_instances
            WHEN NEW.state='pending_parent' BEGIN SELECT RAISE(ABORT,'PRIVATE_REGISTRATION_FAILURE'); END''')
        app.host.sessions._persistence.storage.database.connection.execute(sql)
        with pytest.raises(SessionPersistenceError, match='subagent_save_unconfirmed'):
            await app.host.runs.start(sid, [('user', 'work')])
        rows = app.host.sessions._persistence.storage.database.read_rows('SELECT state FROM naiyue_subagent_instances WHERE session_id=?', (sid,))
        assert rows == ([] if point == 'claim' else [{'state': 'running'}])
        assert len(child.seen) == int(point == 'settle') and len(root.seen) == 1
        assert 'PRIVATE_REGISTRATION_FAILURE' not in caplog.text
        assert not app.host.sessions._sessions.require(sid).is_busy()


@async_test
async def test_cancel_during_claim_io_restores_ready_without_starting_child(monkeypatch, tmp_path):
    import asyncio
    from threading import Event
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'first'}, 'first'), AIMessage(content='done')])
    child = ChildModel(replies=[AIMessage(content='first report')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    async with open_component_application('unused', **options(tmp_path)) as app:
        sid = await app.host.sessions.create('A')
        identity = json.loads(feedback(await app.host.runs.start(sid, [('user', 'first')])).content)['agent_id']
        previous = app.host.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        entered, release = Event(), Event()
        save = app.host.sessions._persistence.storage.subagents.save_subagent
        def hold_claim(record, expected):
            if record['state'] == 'running' and record['last_task'] == 'second':
                entered.set()
                assert release.wait(5), 'Test did not release the admitted SQLite write'
            return save(record, expected)
        monkeypatch.setattr(app.host.sessions._persistence.storage.subagents, 'save_subagent', hold_claim)
        root.replies.extend([call('subagent', {'agent': 'worker', 'task': 'second', 'agent_id': identity}, 'second'), AIMessage(content='must not run')])
        task = asyncio.create_task(app.host.runs.start(sid, [('user', 'second')]))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            saved = app.host.sessions._persistence.storage.subagents.get_subagent(sid, identity)
            assert saved['state'] == 'ready' and saved['checkpoint_id'] == previous['checkpoint_id']
            assert saved['last_delegation_id'] == previous['last_delegation_id']
            assert len(child.seen) == 1 and not app.host.sessions._sessions.require(sid).is_busy()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@async_test
async def test_foreign_id_rejection_cannot_read_archive_or_delete_its_owner(monkeypatch, tmp_path):
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'private'}, 'first'), AIMessage(content='done')])
    child = ChildModel(replies=[AIMessage(content='SESSION_A_PRIVATE_REPORT')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    async with open_component_application('unused', **options(tmp_path)) as app:
        a = await app.host.sessions.create('A')
        identity = json.loads(feedback(await app.host.runs.start(a, [('user', 'private')])).content)['agent_id']
        b = await app.host.sessions.create('A')
        root.replies.extend([call('subagent', {'agent': 'worker', 'task': 'foreign', 'agent_id': identity}, 'foreign'), AIMessage(content='denied')])
        denied = feedback(await app.host.runs.start(b, [('user', 'foreign')]))
        assert denied.status == 'error' and len(child.seen) == 1
        assert await app.host.history.subagents(b) == [] and await app.host.history.chat_subagents(b) == []
        with pytest.raises(LookupError):
            await app.host.history.subagent(b, identity)
        assert all(head['agent_path'] == 'root' for head in app.host.sessions._persistence.storage.conversation.chat_heads(b))
        before = app.host.sessions._persistence.storage.database.read_rows('SELECT checkpoint_id FROM checkpoints WHERE thread_id=?', ('subagent:' + identity,))
        await app.host.sessions.delete(b)
        assert app.host.sessions._persistence.storage.database.read_rows('SELECT checkpoint_id FROM checkpoints WHERE thread_id=?', ('subagent:' + identity,)) == before
        assert (await app.host.history.subagent(a, identity))['messages'][-1].content == 'SESSION_A_PRIVATE_REPORT'
