"""Project identity migration preserves data even when the old directory is absent."""

import sqlite3
from contextlib import closing

from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.backup_schema import check_snapshot
from naiyue_test_agent.workspaces import prepare_workspace


def create(store, root):
    store.sessions.create('S', {'thread_id': 'T', 'model_id': 'M', 'workspace_root': root,
        'active_tool_names': (), 'permission_mode': 'Default'})


def schema17(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        for table, fields in (('naiyue_sessions', ('project_id', 'execution_root_kind')),
                ('naiyue_subagent_instances', ('project_id', 'execution_root', 'execution_root_kind'))):
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
            for field in fields:
                if field in columns:
                    connection.execute(f'ALTER TABLE {table} DROP COLUMN {field}')
        connection.execute('UPDATE naiyue_store_info SET version=17')


def test_bound_schema17_migrates_without_touching_missing_execution_directory(tmp_path):
    root = tmp_path / 'execution'
    root.mkdir()
    location = prepare_workspace(root, data_dir=tmp_path / 'data')
    with ProjectStore(location.database_path) as store:
        store.sessions.bind_workspace(location.workspace_id, root)
        create(store, root)
    schema17(location.database_path)
    assert check_snapshot(location.database_path)['source_schema'] == 17
    root.rename(tmp_path / 'removed')
    with ProjectStore(location.database_path) as store:
        row = store.sessions.get('S')
        assert row['project_id'] == location.workspace_id
        assert row['execution_root_kind'] == 'local'
        assert row['workspace_root'] == str(root)
        assert row['thread_id'] == 'T' and row['state'] == 'ready'
    assert not root.exists()
    assert check_snapshot(location.database_path)['source_schema'] == 18


def test_unbound_schema17_remains_without_project_or_new_identity_file(tmp_path):
    root = tmp_path / 'execution'
    root.mkdir()
    database = tmp_path / 'unbound.sqlite'
    with ProjectStore(database) as store:
        create(store, root)
    schema17(database)
    with ProjectStore(database) as store:
        row = store.sessions.get('S')
        assert row['project_id'] is None and row['execution_root_kind'] == 'local'
        assert store.sessions.get_workspace_binding() is None
    assert not (root / '.naiyue').exists()


def test_local_relocation_does_not_rebind_other_execution_roots(tmp_path):
    root, other = tmp_path / 'main', tmp_path / 'other-execution'
    root.mkdir()
    location = prepare_workspace(root, data_dir=tmp_path / 'data')
    with ProjectStore(location.database_path) as store:
        store.sessions.bind_workspace(location.workspace_id, root)
        create(store, root)
        store.sessions.create('other', {'thread_id': 'other', 'model_id': 'M', 'workspace_root': root,
            'active_tool_names': (), 'permission_mode': 'Default'})
        with store.database.connection:
            store.database.connection.execute("UPDATE naiyue_sessions SET workspace_root=?,execution_root_kind='worktree' WHERE session_id='other'", (str(other),))
        moved = tmp_path / 'moved'
        root.rename(moved)
        store.sessions.bind_workspace(location.workspace_id, moved)
        assert store.sessions.get('S')['workspace_root'] == str(moved)
        assert store.sessions.get('other')['workspace_root'] == str(other)
        assert store.sessions.get('other')['execution_root_kind'] == 'worktree'
        assert {store.sessions.get(sid)['project_id'] for sid in ('S', 'other')} == {location.workspace_id}


def test_schema17_child_identity_is_backfilled_without_reopening_its_directory(monkeypatch, tmp_path):
    import asyncio
    from langchain_core.messages import AIMessage
    from test_async_application import StreamingToolModel, call
    from test_subagent_execution import ChildModel
    from test_execution_root_contract import opened, project
    async def scenario():
        root, location = project(tmp_path)
        model = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'inspect'}), AIMessage(content='saved')])
        child = ChildModel(replies=[AIMessage(content='child saved')])
        async with opened(monkeypatch, tmp_path, model, location=location, child=child) as app:
            sid = await app.acreate_workspace_session('A', root)
            await app.arun(sid, [('user', 'delegate')])
            before, = app._storage.subagents.get_subagent_references(sid).values()
        schema17(location.database_path)
        assert check_snapshot(location.database_path)['source_schema'] == 17
        root.rename(tmp_path / 'removed')
        with ProjectStore(location.database_path) as store:
            after = store.subagents.get_subagent(sid, before['agent_id'])
            assert after == before
            assert after['execution_root'] == str(root) and after['project_id'] == location.workspace_id
        assert not root.exists() and len(child.seen) == 1
    asyncio.run(scenario())


def test_execution_schema_failure_rolls_back_columns_and_version(tmp_path):
    import pytest
    from naiyue_test_agent.storage.execution_storage import initialize
    root = tmp_path / 'main'
    root.mkdir()
    location = prepare_workspace(root, data_dir=tmp_path / 'data')
    with ProjectStore(location.database_path) as store:
        store.sessions.bind_workspace(location.workspace_id, root)
        create(store, root)
    schema17(location.database_path)
    with closing(sqlite3.connect(location.database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.set_authorizer(lambda action, database, table, *rest:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE and table == 'naiyue_subagent_instances' else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            initialize(connection)
        connection.set_authorizer(None)
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 17
        assert 'project_id' not in {r[1] for r in connection.execute('PRAGMA table_info(naiyue_sessions)')}
        assert connection.execute('SELECT thread_id FROM naiyue_sessions').fetchone()[0] == 'T'
