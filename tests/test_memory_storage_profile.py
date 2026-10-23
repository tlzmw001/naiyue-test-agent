"""The temporary profile uses real repositories and official checkpoints in RAM."""
import asyncio
import sqlite3

import pytest

from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.project_store import ProjectStore


def test_memory_database_never_enters_disk_guards(monkeypatch, tmp_path):
    from naiyue_test_agent.storage import database as module
    def forbidden(*args, **kwargs):
        pytest.fail('memory profile entered a disk guard')
    monkeypatch.setattr(module, 'open_store_file', forbidden)
    monkeypatch.setattr(module, 'check_restore_marker', forbidden)
    with Database.open_memory() as database:
        assert database.path is None
        assert database.uri.startswith('file:/naiyue-') and database.uri.endswith('?vfs=memdb')
        assert database.connection.execute('PRAGMA journal_mode').fetchone()[0] == 'memory'
        assert database.read_rows('SELECT version FROM naiyue_store_info') == [{'version': 18}]
    assert list(tmp_path.iterdir()) == []


def test_memory_store_uses_same_repo_types_and_is_isolated(tmp_path):
    with ProjectStore(tmp_path / 'disk.sqlite') as disk, ProjectStore.in_memory() as first, ProjectStore.in_memory() as second:
        for name in ('sessions', 'conversation', 'replies', 'subagents', 'usage', 'diagnostics', 'memory', 'runs'):
            assert type(getattr(first, name)) is type(getattr(disk, name))
        assert first.path is second.path is None
        assert first.database.uri != second.database.uri
        with first.database.transaction(write=True) as connection:
            connection.execute('CREATE TABLE lifetime(value INTEGER)')
            connection.execute('INSERT INTO lifetime VALUES(?)', (7,))
        assert second.database.read_rows("SELECT name FROM sqlite_master WHERE name='lifetime'") == []


def test_official_saver_shares_memory_store_and_last_close_discards_data():
    async def scenario():
        from naiyue_test_agent.storage.resources import open_storage
        async with open_storage(None) as resources:
            database = resources.store.database
            uri = database.uri
            assert resources.checkpointer.conn is not database.connection
            assert database.read_rows("SELECT name FROM sqlite_master WHERE name='checkpoints'") == [{'name': 'checkpoints'}]
            await resources.checkpointer.conn.execute('CREATE TABLE lifetime(value INTEGER)')
            await resources.checkpointer.conn.execute('INSERT INTO lifetime VALUES(?)', (9,))
            await resources.checkpointer.conn.commit()
            assert database.read_rows('SELECT value FROM lifetime') == [{'value': 9}]
        with pytest.raises(sqlite3.ProgrammingError):
            database.connection.execute('SELECT 1')
        with pytest.raises(ValueError, match='no active connection'):
            await resources.checkpointer.conn.execute('SELECT 1')
        with sqlite3.connect(uri, uri=True) as reopened:
            assert reopened.execute("SELECT name FROM sqlite_master").fetchall() == []
    asyncio.run(scenario())


@pytest.mark.parametrize('disk', [False, True])
def test_saver_setup_failure_closes_store_before_model_start(monkeypatch, tmp_path, disk):
    async def scenario():
        from naiyue_test_agent import application
        from naiyue_test_agent.application.assembly import build_application
        from naiyue_test_agent.storage import resources as module
        stores = []
        original_assemble = ProjectStore._assemble
        failure = RuntimeError('PRIVATE_SETUP')
        def assemble(store, database):
            original_assemble(store, database)
            stores.append(store)
        async def fail_setup(saver):
            raise failure
        def forbidden(*args):
            pytest.fail('model started after checkpoint setup failure')
        monkeypatch.setattr(ProjectStore, '_assemble', assemble)
        monkeypatch.setattr(module.AsyncSqliteSaver, 'setup', fail_setup)
        monkeypatch.setattr(application, 'load_model_catalog', forbidden)
        with pytest.raises(RuntimeError) as caught:
            await build_application('unused', database_path=tmp_path / 'disk.sqlite' if disk else None,
                                    tools=[], system_prompt='', response_format=None)
        assert caught.value is failure and len(stores) == 1
        assert stores[0].database._closed
        if disk:
            with ProjectStore(tmp_path / 'disk.sqlite'):
                pass
        else:
            with sqlite3.connect(stores[0].database.uri, uri=True) as reopened:
                assert reopened.execute('SELECT name FROM sqlite_master').fetchall() == []
    asyncio.run(scenario())


def test_cancel_during_async_connection_open_drains_then_closes(monkeypatch):
    async def scenario():
        from naiyue_test_agent.storage import resources as module
        entered, release = asyncio.Event(), asyncio.Event()
        connections, stores = [], []
        connect = module.aiosqlite.Connection._connect
        assemble = ProjectStore._assemble
        def capture(store, database):
            assemble(store, database)
            stores.append(store)
        async def blocked(connection):
            await connect(connection)
            connections.append(connection)
            entered.set()
            await release.wait()
            return connection
        monkeypatch.setattr(ProjectStore, '_assemble', capture)
        monkeypatch.setattr(module.aiosqlite.Connection, '_connect', blocked)
        async def open_and_close():
            async with module.open_storage(None):
                pytest.fail('cancelled resource must not reach its caller')
        task = asyncio.create_task(open_and_close())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel('cancel async connection acquisition')
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.args == ('cancel async connection acquisition',)
            assert stores[0].database._closed
            with pytest.raises(ValueError, match='no active connection'):
                await connections[0].execute('SELECT 1')
            with sqlite3.connect(stores[0].database.uri, uri=True) as reopened:
                assert reopened.execute('SELECT name FROM sqlite_master').fetchall() == []
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
