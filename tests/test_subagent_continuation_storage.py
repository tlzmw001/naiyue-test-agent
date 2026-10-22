"""Registry ownership and pointers survive pruning, backup and actual restore."""

import asyncio
import json
import sqlite3
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.session_storage import SessionStorage
from naiyue_test_agent.checkpoint_settings import CheckpointRetentionSettings
from naiyue_test_agent.storage_backup import create_backup, validate_backup
from naiyue_test_agent.storage_backup_schema import check_snapshot
from naiyue_test_agent.storage_restore import restore_backup
from naiyue_test_agent.workspaces import prepare_workspace
from test_async_application import StreamingToolModel
from test_subagent_execution import ChildModel
from test_subagent_restart import options
from test_subagent_continuation_boundaries import delegate, models


@async_test
async def test_prune_backup_restore_reopen_then_continue_same_instance(monkeypatch, tmp_path):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    database = tmp_path / 'state.sqlite'
    location = prepare_workspace(workspace, data_dir=tmp_path / 'data', database_path=database)
    root = StreamingToolModel(replies=[])
    child = ChildModel(replies=[AIMessage(content=x) for x in ('first', 'second', 'not in backup', 'after restore')])
    models(monkeypatch, root, child)
    config = options(tmp_path)
    config['settings'] = config['settings'].model_copy(update={
        'checkpoint_retention': CheckpointRetentionSettings(trigger_checkpoints=2, max_delete_checkpoints=128)})
    with SessionStorage(database) as storage:
        storage.bind_workspace(location.workspace_id, workspace)
    async with open_async_persistent_application('unused', **config) as app:
        sid = await app.acreate_workspace_session('A', workspace)
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        thread = 'subagent:' + identity
        await delegate(app, sid, root, task='second', identity=identity)
        confirmed = app._storage.get_subagent(sid, identity)
        assert app._storage._read('SELECT checkpoint_id FROM checkpoints WHERE thread_id=?', (thread,)) == [
            {'checkpoint_id': confirmed['checkpoint_id']}]
        saved = await asyncio.to_thread(create_backup, app._storage.connection, database, target_schema=14)
        assert validate_backup(database, saved['backup_id'])['source_schema'] == 14
        await delegate(app, sid, root, task='discard by restore', identity=identity)
    before = (len(root.seen), len(child.seen))
    result = restore_backup(location, saved['backup_id'], confirm=lambda _: saved['backup_id'])
    assert result['status'] == 'completed' and (len(root.seen), len(child.seen)) == before
    async with open_async_persistent_application('unused', **config) as app:
        await app.arestore_session(sid)
        assert app._storage.get_subagent(sid, identity) == confirmed
        assert (len(root.seen), len(child.seen)) == before
        assert json.loads((await delegate(app, sid, root, task='after restore', identity=identity)).content)['report'] == 'after restore'
        child_history = await app.aget_subagent_history(sid, identity)
        assert [m.content for m in child_history['messages']] == ['first', 'first', 'second', 'second', 'after restore', 'after restore']
        await app.adelete_session(sid)
        assert not app._storage.get_subagent_references(sid)
        assert not app._storage._read('SELECT 1 FROM checkpoints WHERE thread_id=?', (thread,))
        assert not app._storage._read('SELECT 1 FROM writes WHERE thread_id=?', (thread,))


@pytest.mark.parametrize('corruption', ['owner', 'identity', 'path', 'checkpoint', 'missing_column'])
@async_test
async def test_backup_rejects_invalid_registry_without_loading_payload(monkeypatch, tmp_path, corruption):
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='saved')])
    models(monkeypatch, root, child)
    async with open_async_persistent_application('unused', **options(tmp_path)) as app:
        sid = await app.acreate_session('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        snapshot = tmp_path / 'snapshot.sqlite'
        with closing(sqlite3.connect(snapshot)) as target:
            app._storage.connection.backup(target)
            target.execute('PRAGMA journal_mode=DELETE')
            if corruption == 'missing_column':
                target.execute('ALTER TABLE naiyue_subagent_instances DROP COLUMN last_delegation_id')
            else:
                column, value = {'owner': ('session_id', 'absent'), 'identity': ('agent_id', 'bad'),
                    'path': ('agent_path', 'root/other/' + identity), 'checkpoint': ('checkpoint_id', 'missing')}[corruption]
                target.execute(f'UPDATE naiyue_subagent_instances SET {column}=?', (value,))
            target.commit()
        with pytest.raises(ValueError, match='backup_invalid'):
            check_snapshot(snapshot)


@async_test
async def test_retention_rechecks_registry_pointer_before_delete(monkeypatch, tmp_path):
    from naiyue_test_agent.application import checkpoint_retention as retention
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='one'), AIMessage(content='two')])
    models(monkeypatch, root, child)
    config = options(tmp_path)
    config['settings'] = config['settings'].model_copy(update={
        'checkpoint_retention': CheckpointRetentionSettings(enabled=False)})
    async with open_async_persistent_application('unused', **config) as app:
        sid = await app.acreate_session('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        await delegate(app, sid, root, task='second', identity=identity)
        parent = app._storage.get(sid)
        settings = CheckpointRetentionSettings(trigger_checkpoints=2, max_delete_checkpoints=128)
        inventory = retention._inventory(app._storage, sid, parent['run_id'], settings)
        protected = await retention._protected(app._agent, app._storage, inventory, settings)
        candidates = retention._candidates(app._storage, inventory, inventory['scopes'], protected, settings)
        key = next(key for key, _ in candidates if key[0] == 'subagent:' + identity)
        app._storage.connection.execute('UPDATE naiyue_subagent_instances SET checkpoint_id=?,version=version+1 WHERE session_id=? AND agent_id=?',
            (key[2], sid, identity))
        app._storage.connection.commit()
        before = app._storage._read('SELECT * FROM checkpoints')
        with pytest.raises(retention.RetentionSkipped, match='registry_changed'):
            retention._commit(app._storage, inventory, candidates, protected)
        assert app._storage._read('SELECT * FROM checkpoints') == before
