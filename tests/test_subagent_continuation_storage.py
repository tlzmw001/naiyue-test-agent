"""Registry ownership and pointers survive pruning, backup and actual restore."""

import asyncio
import json
import sqlite3
from contextlib import closing

import pytest

from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.checkpoint_settings import CheckpointRetentionSettings
from naiyue_test_agent.storage.backup import create_backup, validate_backup
from naiyue_test_agent.storage.backup_schema import check_snapshot
from naiyue_test_agent.storage.restore import restore_backup
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
    with ProjectStore(database) as storage:
        storage.sessions.bind_workspace(location.workspace_id, workspace)
    async with open_component_application('unused', **config) as app:
        sid = await app.host.sessions.create_workspace('A', workspace)
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        thread = 'subagent:' + identity
        await delegate(app, sid, root, task='second', identity=identity)
        confirmed = app.host.sessions._persistence.storage.subagents.get_subagent(sid, identity)
        assert app.host.sessions._persistence.storage.database.read_rows('SELECT checkpoint_id FROM checkpoints WHERE thread_id=?', (thread,)) == [
            {'checkpoint_id': confirmed['checkpoint_id']}]
        saved = await asyncio.to_thread(create_backup, app.host.sessions._persistence.storage.database.connection, database, target_schema=CURRENT_SCHEMA)
        assert validate_backup(database, saved['backup_id'])['source_schema'] == CURRENT_SCHEMA
        await delegate(app, sid, root, task='discard by restore', identity=identity)
    before = (len(root.seen), len(child.seen))
    result = restore_backup(location, saved['backup_id'], confirm=lambda _: saved['backup_id'])
    assert result['status'] == 'completed' and (len(root.seen), len(child.seen)) == before
    async with open_component_application('unused', **config) as app:
        await app.host.sessions.restore(sid)
        assert app.host.sessions._persistence.storage.subagents.get_subagent(sid, identity) == confirmed
        assert (len(root.seen), len(child.seen)) == before
        assert json.loads((await delegate(app, sid, root, task='after restore', identity=identity)).content)['report'] == 'after restore'
        child_history = await app.host.history.subagent(sid, identity)
        assert [m.content for m in child_history['messages']] == ['first', 'first', 'second', 'second', 'after restore', 'after restore']
        await app.host.sessions.delete(sid)
        assert not app.host.sessions._persistence.storage.subagents.get_subagent_references(sid)
        assert not app.host.sessions._persistence.storage.database.read_rows('SELECT 1 FROM checkpoints WHERE thread_id=?', (thread,))
        assert not app.host.sessions._persistence.storage.database.read_rows('SELECT 1 FROM writes WHERE thread_id=?', (thread,))


@pytest.mark.parametrize('corruption', ['owner', 'identity', 'path', 'checkpoint', 'missing_column'])
@async_test
async def test_backup_rejects_invalid_registry_without_loading_payload(monkeypatch, tmp_path, corruption):
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='saved')])
    models(monkeypatch, root, child)
    async with open_component_application('unused', **options(tmp_path)) as app:
        sid = await app.host.sessions.create('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        snapshot = tmp_path / 'snapshot.sqlite'
        with closing(sqlite3.connect(snapshot)) as target:
            app.host.sessions._persistence.storage.database.connection.backup(target)
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
    from naiyue_test_agent.storage import checkpoint_retention as retention
    root, child = StreamingToolModel(replies=[]), ChildModel(replies=[AIMessage(content='one'), AIMessage(content='two')])
    models(monkeypatch, root, child)
    config = options(tmp_path)
    config['settings'] = config['settings'].model_copy(update={
        'checkpoint_retention': CheckpointRetentionSettings(enabled=False)})
    async with open_component_application('unused', **config) as app:
        sid = await app.host.sessions.create('A')
        identity = json.loads((await delegate(app, sid, root)).content)['agent_id']
        await delegate(app, sid, root, task='second', identity=identity)
        parent = app.host.sessions._persistence.storage.sessions.get(sid)
        settings = CheckpointRetentionSettings(trigger_checkpoints=2, max_delete_checkpoints=128)
        inventory = retention._inventory(app.host.sessions._persistence.storage, sid, parent['run_id'], settings)
        protected = await retention._protected(app.host.runs._agent, app.host.sessions._persistence.storage, inventory, settings)
        candidates = retention._candidates(app.host.sessions._persistence.storage, inventory, inventory['scopes'], protected, settings)
        key = next(key for key, _ in candidates if key[0] == 'subagent:' + identity)
        app.host.sessions._persistence.storage.database.connection.execute('UPDATE naiyue_subagent_instances SET checkpoint_id=?,version=version+1 WHERE session_id=? AND agent_id=?',
            (key[2], sid, identity))
        app.host.sessions._persistence.storage.database.connection.commit()
        before = app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM checkpoints')
        with pytest.raises(retention.RetentionSkipped, match='registry_changed'):
            retention._commit(app.host.sessions._persistence.storage, inventory, candidates, protected)
        assert app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM checkpoints') == before
