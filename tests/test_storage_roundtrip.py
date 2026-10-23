"""Upgrade, explicit restore, and official reopen keep the original contracts."""

from contextlib import closing
import sqlite3
import sys

import pytest

from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA

from native_application_helpers import async_test
from tests.storage_backup_fixture import artifact, closed_store
from tests.legacy_store_schema import remove_memory_schema


@pytest.mark.parametrize('source_schema', range(4, CURRENT_SCHEMA))
def test_all_supported_legacy_stores_roundtrip_without_deserializing_opaque_payload(tmp_path, source_schema):
    from naiyue_test_agent.storage.session_storage import SessionStorage
    from naiyue_test_agent.storage.backup import list_backups
    from naiyue_test_agent.storage.restore import restore_backup
    from naiyue_test_agent.workspaces import locate_workspace
    database, root, identity = closed_store(tmp_path, version=source_schema)
    with SessionStorage(database) as storage:
        assert storage.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == CURRENT_SCHEMA
        backup, = list_backups(database, workspace_id=identity)
        assert backup['manifest']['source_schema'] == source_schema
        storage.connection.execute("UPDATE naiyue_sessions SET model_id='later-model'")
        storage.connection.commit()
    backup_id = backup['backup_id']
    snapshot = artifact(database, backup_id) / 'database.sqlite'
    original_backup = snapshot.read_bytes()
    location = locate_workspace(root, data_dir=None, database_path=database)
    result = restore_backup(location, backup_id, confirm=lambda _: backup_id)
    assert result['status'] == 'completed' and database.read_bytes() == original_backup
    with SessionStorage(database) as reopened:
        assert reopened.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == CURRENT_SCHEMA
        assert reopened.get('S')['model_id'] == 'M' and reopened.get('S')['state'] == 'running'
        assert reopened.connection.execute('SELECT checkpoint FROM checkpoints').fetchone()[0] == b'opaque-checkpoint'
        assert reopened.connection.execute('SELECT value FROM writes').fetchone()[0] == b'opaque-value'
        backups = list_backups(database, workspace_id=identity)
        assert len(backups) == 2 and len({row['backup_id'] for row in backups}) == 2
        assert all(row['manifest']['source_schema'] == source_schema for row in backups)
    assert snapshot.read_bytes() == original_backup


@pytest.mark.parametrize('decision', ['approve', 'reject'])
@async_test
async def test_real_graph_restore_reopen_keeps_history_usage_child_and_approval(tmp_path, decision):
    from scripts.release_storage_smoke import storage_scenario
    from test_async_application import StreamingToolModel
    report = await storage_scenario(tmp_path, StreamingToolModel,
                                    [sys.executable, '-m', 'naiyue_test_agent.cli'], decision=decision)
    assert report['storage_roundtrip'] == 'passed'
    assert report['history_reopen'] == report['usage_reopen'] == report['child_reopen'] == 'passed'
    assert report['approval_reopen'] == 'passed'
    assert report['history_continuation'] == 'passed'
    assert report['restore_phase_calls'] == {'model': 0, 'write': 0, 'auto': 0}
    assert report['approved_write_calls'] == int(decision == 'approve')
    assert report['provider_requests'] == 0


@pytest.mark.parametrize('point', ['approval', 'committed'])
@async_test
async def test_crashed_child_survives_restore_without_replaying_uncertain_side_effect(tmp_path, monkeypatch, point):
    import asyncio
    import json
    import os
    from pathlib import Path
    import subprocess
    from unittest.mock import patch
    import importlib
    from langchain_core.messages import ToolMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.storage.session_storage import SessionStorage, SessionPersistenceError
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.storage.backup import list_backups
    from naiyue_test_agent.storage.restore import restore_backup
    from naiyue_test_agent.workspaces import prepare_workspace
    from test_async_application import StreamingToolModel
    from test_subagent_execution import ChildModel
    import test_subagent_restart as fixture

    workspace = tmp_path / 'work'
    workspace.mkdir()
    (tmp_path / 'outside.txt').write_text('unapproved', encoding='utf-8')
    database = tmp_path / 'state.sqlite'
    location = prepare_workspace(workspace, data_dir=tmp_path / 'data', database_path=database)
    with SessionStorage(database) as storage:
        storage.bind_workspace(location.workspace_id, workspace)
    repository = Path(__file__).resolve().parents[1]
    exited = await asyncio.to_thread(subprocess.run, [sys.executable, fixture.__file__, point, str(tmp_path)],
        cwd=repository, env={**os.environ, 'PYTHONPATH': os.pathsep.join((str(repository), str(repository / 'tests')))},
        capture_output=True, text=True, timeout=30)
    assert exited.returncode == 91, exited.stderr
    info = json.loads((tmp_path / 'crash.json').read_text())
    with SessionStorage(database) as storage:
        assert storage.get(info['session_id'])['state'] == 'running'
    with closing(sqlite3.connect(database)) as connection:
        remove_memory_schema(connection)
        connection.execute('DROP TABLE naiyue_subagent_instances')
        connection.execute('ALTER TABLE naiyue_model_invocations DROP COLUMN duration_ms')
        connection.execute('UPDATE naiyue_store_info SET version=12')
        connection.commit()
    with SessionStorage(database):
        backup, = list_backups(database)
    restore_backup(location, backup['backup_id'], confirm=lambda _: backup['backup_id'])
    parent, child = StreamingToolModel(replies=[]), ChildModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': parent, 'B': child}))
    effects = tmp_path / 'executions'
    before = effects.read_text() if effects.exists() else None
    write_module = importlib.import_module('naiyue_test_agent.tools.write')
    with patch.object(write_module, '_write', wraps=write_module._write) as written:
        async with open_async_persistent_application('unused', **fixture.options(tmp_path)) as app:
            events = []
            app.subscribe_events(events.append)
            history = await app.aget_session_history(info['session_id'])
            assert history['recovery']['kind'] == 'interrupted_execution'
            with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
                await app.arecover_session(info['session_id'], checkpoint_id=history['checkpoint_id'], action='restore_approval')
            await app.arecover_session(info['session_id'], checkpoint_id=history['checkpoint_id'], action='continue')
            restored = await app.aget_messages(info['session_id'])
            feedback = restored[-1]
            assert isinstance(feedback, ToolMessage) and feedback.tool_call_id == 'outer' and feedback.status == 'error'
            assert feedback.additional_kwargs['naiyue_recovery']['reason'] == 'result_unconfirmed'
            assert not app.pending_approval_batches(info['session_id'])
            children = await app.aget_chat_subagents(info['session_id'])
            assert any(row['agent_id'] == info['child_thread_id'].removeprefix('subagent:') for row in children)
            assert not parent.seen and not child.seen and not events and written.call_count == 0
    assert (effects.read_text() if effects.exists() else None) == before
    assert (workspace / 'effect.txt').exists() is (point == 'committed')
