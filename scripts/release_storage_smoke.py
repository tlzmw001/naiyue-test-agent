"""Temporary-data upgrade/restore/reopen acceptance shared by pytest and installed CI."""

import asyncio
from contextlib import asynccontextmanager, closing
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from unittest.mock import patch


# Fixed names only; snapshot comparison never deserializes checkpoint BLOBs.
_TABLES = ('naiyue_store_info', 'naiyue_workspace', 'naiyue_sessions', 'checkpoints', 'writes',
    'naiyue_chat_sync', 'naiyue_chat_messages', 'naiyue_model_usage_runs', 'naiyue_model_invocations',
    'naiyue_diagnostic_runs', 'naiyue_diagnostic_events', 'naiyue_reply_boundaries', 'naiyue_regenerations')


def _facts(database):
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return {table: sorted([dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')], key=repr)
                for table in _TABLES}


async def storage_scenario(root, Model, command, *, decision='approve'):
    """Only call with a disposable directory; construct real graphs and use the real CLI."""
    from langchain_core.messages import AIMessage, ToolMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.errors import SessionPersistenceError
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.storage.backup import list_backups
    from naiyue_test_agent.tools import (SubAgentSpec, create_subagent_tool,
        create_session_read_tool, create_session_write_tool)
    from naiyue_test_agent.workspaces import prepare_workspace

    assert decision in ('approve', 'reject')
    root = Path(root).resolve(strict=True)
    workspace, database = root / 'storage-workspace', root / 'storage.sqlite'
    workspace.mkdir()
    (workspace / 'note.txt').write_text('child-source', encoding='utf-8')
    outside = root / 'storage-outside.txt'
    location = prepare_workspace(workspace, data_dir=root / 'storage-data', database_path=database)
    settings = AppSettings(subagents=(SubAgentSpec('reader', 'Read the note', 'Report the note.',
                                                   ('read',), model_id='child'),))
    tools = [create_subagent_tool(), create_session_read_tool(), create_session_write_tool()]

    @asynccontextmanager
    async def opened(parent, child):
        with patch.object(application, 'load_model_catalog',
                          lambda _: ModelCatalog('root', {'root': parent, 'child': child})):
            async with open_async_persistent_application('unused', database_path=database, tools=tools,
                    system_prompt='', response_format=None, settings=settings,
                    workspace_binding={'workspace_id': location.workspace_id, 'root_path': workspace}) as app:
                yield app

    parent = Model(replies=[
        AIMessage(content='known answer', usage_metadata={'input_tokens': 101, 'output_tokens': 7, 'total_tokens': 108}),
        AIMessage(content='unknown answer'),
        AIMessage(content='', tool_calls=[{'name': 'subagent', 'args': {'agent': 'reader', 'task': 'read note.txt'}, 'id': 'delegate'}]),
        AIMessage(content='root report'),
        AIMessage(content='', tool_calls=[{'name': 'write', 'args': {'path': str(outside), 'content': 'approved once'}, 'id': 'approval-call'}]),
    ])
    child = Model(replies=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': 'note.txt'}, 'id': 'child-read'}]),
        AIMessage(content='child report', usage_metadata={'input_tokens': 11, 'output_tokens': 2, 'total_tokens': 13})])
    async with opened(parent, child) as app:
        normal = await app.acreate_workspace_session('root', workspace)
        await app.arun(normal, [('user', 'first')])
        await app.arun(normal, [('user', 'second')])
        await app.aupdate_session_presentation(normal, title='Saved title', pinned=True)
        messages = await app.aget_messages(normal)
        usage = await app.aget_model_usage(normal, model_limit=20)
        assert usage['metrics']['input_tokens'] == {'known_sum': '101', 'reported_calls': 1, 'unknown_calls': 1}
        delegated = await app.acreate_workspace_session('root', workspace)
        result = await app.arun(delegated, [('user', 'delegate one read')])
        assert result['messages'][-1].content == 'root report'
        children = await app.aget_chat_subagents(delegated)
        assert len(children) == 1
        child_id = children[0]['agent_id']
        child_page = await app.aget_chat_page(delegated, agent_id=child_id, limit=50, body_bytes=16384, preview_chars=2048)
        assert child_page['records'][-1]['message_id']
        pending = await app.acreate_workspace_session('root', workspace)
        await app.arun(pending, [('user', 'write outside after approval')])
        assert len(app.pending_tool_approvals(pending)) == 1 and not outside.exists()
        pending_row = app._storage.sessions.get(pending)
        pending_state = await app._agent.aget_state({'configurable': {'thread_id': pending_row['thread_id']}})
        pending_checkpoint = await app._agent.checkpointer.aget_tuple(pending_state.config)
        assert not any(isinstance(message, ToolMessage) for message in pending_state.values['messages'])
    assert len(parent.seen) == 5 and len(child.seen) == 2 and not parent.replies and not child.replies

    # This is fixture construction on a CLOSED disposable database, never a product downgrade command.
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('ALTER TABLE naiyue_sessions DROP COLUMN project_id')
        connection.execute('ALTER TABLE naiyue_sessions DROP COLUMN execution_root_kind')
        connection.execute('ALTER TABLE naiyue_chat_messages DROP COLUMN occurred_at')
        for table in ('naiyue_memory_overviews','naiyue_memory_progress','naiyue_memory_versions','naiyue_memory_suppression','naiyue_memory_search', 'naiyue_memories', 'naiyue_memory_sources', 'naiyue_memory_info'):
            connection.execute(f'DROP TABLE {table}')
        connection.execute('DROP TABLE naiyue_subagent_instances')
        connection.execute('ALTER TABLE naiyue_model_invocations DROP COLUMN duration_ms')
        connection.execute('UPDATE naiyue_store_info SET version=12')
        connection.commit()
    before = _facts(database)
    with ProjectStore(database) as storage:
        backup, = list_backups(database, workspace_id=location.workspace_id)
        assert backup['manifest']['source_schema'] == 12
        storage.sessions.update_presentation(normal, title='After backup')
    library_side_effect = root / 'outside-after-backup.txt'
    library_side_effect.write_text('not rolled back', encoding='utf-8')
    database.write_bytes(b'corrupt target retained as original')
    original = database.read_bytes()
    home = root / 'storage-maintenance-home'
    (home / '.naiyue-test-agent').mkdir(parents=True)
    for name in ('settings.toml', 'models.json'):
        (home / '.naiyue-test-agent' / name).write_text('invalid configuration [', encoding='utf-8')
    environment = {**os.environ, 'HOME': str(home), 'USERPROFILE': str(home), 'PYTHONUTF8': '1'}
    options = ['--workspace', str(workspace), '--session-db', str(database)]
    listing = await asyncio.to_thread(subprocess.run, [*command, 'storage', 'backups', *options],
        env=environment, capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(listing.stdout)['backups'] == [backup]
    restore_command = [*command, 'storage', 'restore', backup['backup_id'], *options]
    declined = await asyncio.to_thread(subprocess.run, restore_command, input='no\n', env=environment,
        capture_output=True, text=True, timeout=30)
    assert declined.returncode == 1 and json.loads(declined.stdout.splitlines()[-1]) == {'error': 'restore_cancelled'}
    assert database.read_bytes() == original and not database.with_name(database.name + '.maintenance').exists()
    restored = await asyncio.to_thread(subprocess.run, restore_command, input=backup['backup_id'] + '\n',
        env=environment, capture_output=True, text=True, check=True, timeout=30)
    outcome = json.loads(restored.stdout.splitlines()[-1])
    assert outcome['status'] == 'completed' and outcome['backup_id'] == backup['backup_id']
    assert (Path(outcome['original_directory']) / database.name).read_bytes() == original
    assert not (database.with_name(database.name + '.maintenance') / 'restore.json').exists()
    assert _facts(database) == before
    assert library_side_effect.read_text(encoding='utf-8') == 'not rolled back' and not outside.exists()

    parent, child = Model(replies=[]), Model(replies=[])
    write_module = importlib.import_module('naiyue_test_agent.tools.write')
    with patch.object(write_module, '_write', wraps=write_module._write) as written:
        with patch.object(AutoPermissionReviewer, 'areview', side_effect=AssertionError('Unexpected Auto call')) as auto:
            async with opened(parent, child) as app:
                current = _facts(database)
                from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA
                assert current.pop('naiyue_store_info') == [{'version': CURRENT_SCHEMA}]
                assert app._storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_subagent_instances').fetchone()[0] == 0
                expected = {key: value for key, value in before.items() if key != 'naiyue_store_info'}
                assert all(row.pop('duration_ms') is None for row in current['naiyue_model_invocations'])
                assert all(row.pop('occurred_at') is None for row in current['naiyue_chat_messages'])
                for row in current['naiyue_sessions']:
                    assert row.pop('project_id') == location.workspace_id
                    assert row.pop('execution_root_kind') == 'local'
                assert current == expected
                assert len(list_backups(database)) == 2
                events = []
                app.subscribe_events(events.append)
                await app.arestore_session(normal)
                assert await app.aget_messages(normal) == messages
                row = app._storage.sessions.get(normal)
                assert row['title'] == 'Saved title' and row['pinned']
                summary = await app.aget_model_usage(normal, model_limit=20)
                assert summary['metrics'] == usage['metrics']
                assert summary['duration'] == {'known_sum_ms': None, 'reported_calls': 0, 'unknown_calls': 2}
                assert summary['status_counts'] == usage['status_counts'] and summary['recorded_calls'] == 2
                assert await app.alist_diagnostic_runs(normal)
                await app.arestore_session(delegated)
                assert await app.aget_chat_subagents(delegated) == [
                    {**child, 'resume_available': False, 'resume_reason': 'legacy_instance'} for child in children]
                assert await app.aget_chat_page(delegated, agent_id=child_id, limit=50,
                    body_bytes=16384, preview_chars=2048) == child_page
                try:
                    await app.arestore_session(pending)
                except SessionPersistenceError as error:
                    assert str(error) == 'session_recovery_blocked'
                else:
                    raise AssertionError('Pending approval was restored without recovery')
                history = await app.aget_session_history(pending)
                assert history['recovery']['kind'] == 'pending_approval'
                await app.arecover_session(pending, checkpoint_id=history['checkpoint_id'], action='restore_approval')
                assert await app._agent.checkpointer.aget_tuple(pending_state.config) == pending_checkpoint
                memory = app._sessions._sessions[pending]
                assert memory.resolve_permission_context()['session_allow'] == []
                assert app.pending_tool_approvals(pending)[0]['tool_call_id'] == 'approval-call'
                assert not parent.seen and not child.seen and written.call_count == auto.call_count == 0 and events == []
                restore_phase_calls = {'model': len(parent.seen) + len(child.seen),
                                       'write': written.call_count, 'auto': auto.call_count}
                parent.replies.append(AIMessage(content='after decision'))
                result = await app.aresume_tool_approval(pending, [decision])
                feedback, = [message for message in result['messages'] if isinstance(message, ToolMessage)]
                assert feedback.tool_call_id == 'approval-call'
                assert feedback.status == ('success' if decision == 'approve' else 'error')
                assert written.call_count == int(decision == 'approve') and not child.seen and len(parent.seen) == 1
                assert outside.exists() is (decision == 'approve')
                if outside.exists():
                    assert outside.read_text(encoding='utf-8') == 'approved once'
                assert app._storage.sessions.get(pending)['state'] == 'ready' and not app.pending_tool_approvals(pending)
                parent.replies.append(AIMessage(content='continued after restore'))
                continued = await app.arun(normal, [('user', 'after restore')])
                assert continued['messages'][:-2] == messages
                assert [message.content for message in continued['messages'][-2:]] == ['after restore', 'continued after restore']
                assert [message.content for message in parent.seen[-1][-5:]] == [message.content for message in messages] + ['after restore']
                assert len(parent.seen) == 2 and not child.seen and written.call_count == int(decision == 'approve')
                assert await app.aget_messages(normal) == continued['messages']
                saved = app._storage.sessions.get(normal)
                assert saved['state'] == 'ready' and saved['checkpoint_id']
    return {'storage_backups': 'passed', 'migration_backup': 'passed', 'storage_restore': 'passed',
        'storage_roundtrip': 'passed', 'history_reopen': 'passed', 'usage_reopen': 'passed',
        'child_reopen': 'passed', 'approval_reopen': 'passed', 'history_continuation': 'passed', 'restore_phase_calls': restore_phase_calls,
        'approved_write_calls': written.call_count, 'provider_requests': 0}
