"""Budgets, state changes and graph recovery do not turn maintenance into authority."""

import json

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from test_application_run_events import ToolModel
from test_persistent_sessions import build
from test_checkpoint_retention import configure, checkpoints
from naiyue_test_agent.storage import checkpoint_retention as retention
from naiyue_test_agent.checkpoint_settings import CheckpointRetentionSettings
from naiyue_test_agent.tools import create_session_read_tool


@pytest.mark.parametrize('budget', ['count', 'bytes'])
@async_test
async def test_one_finish_obeys_shared_budget_with_real_database_deletions(monkeypatch, tmp_path, budget):
    path = tmp_path / 'state.sqlite'
    configure(monkeypatch, enabled=False)
    model = ToolModel(responses=[AIMessage(content=f'reply {n} ' * 5) for n in range(25)])
    async with build(monkeypatch, path, model) as app:
        sid = await app.acreate_session('A')
        for n in range(20):
            await app.arun(sid, [('user', f'input {n} ' * 5)])
    settings = configure(monkeypatch, trigger_checkpoints=2,
        max_delete_checkpoints=1 if budget == 'count' else 128, max_delete_bytes=65536)
    measurements = []
    original = retention._commit
    def measured(storage, inventory, candidates, protected):
        before = storage.database.connection.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0]
        result = original(storage, inventory, candidates, protected)
        after = storage.database.connection.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0]
        measurements.append((before - after, result))
        return result
    monkeypatch.setattr(retention, '_commit', measured)
    async with build(monkeypatch, path, model) as app:
        await app.arestore_session(sid)
        await app.arun(sid, [('user', 'next')])
    assert len(measurements) == 1
    actual, result = measurements[0]
    assert actual == result['deleted_checkpoints'] > 0
    assert actual <= settings.checkpoint_retention.max_delete_checkpoints
    assert result['deleted_bytes'] <= settings.checkpoint_retention.max_delete_bytes
    if budget == 'count':
        assert actual == 1
    else:
        assert actual < 128


@async_test
async def test_pending_approval_and_writes_survive_close_reopen(monkeypatch, tmp_path):
    configure(monkeypatch, trigger_checkpoints=2)
    root = tmp_path / 'root'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('synthetic allowed result')
    path = tmp_path / 'state.sqlite'
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'id': 'outside', 'args': {'path': str(outside)}}]), AIMessage(content='done')])
    tools = [create_session_read_tool()]
    async with build(monkeypatch, path, model, tools) as app:
        sid = await app.acreate_workspace_session('A', root)
        paused = await app.arun(sid, [('user', 'read')])
        assert paused['approval_requests']
        row = app._storage.sessions.get(sid)
        thread = row['thread_id']
        before = app._storage.database.read_rows('SELECT * FROM checkpoints WHERE thread_id=?', (thread,))
        writes = app._storage.database.read_rows('SELECT * FROM writes WHERE thread_id=?', (thread,))
    async with build(monkeypatch, path, model, tools) as app:
        saved = await app._agent.aget_state({'configurable': {'thread_id': thread}})
        await app.arecover_session(sid, checkpoint_id=saved.config['configurable']['checkpoint_id'], action='restore_approval')
        assert app._storage.database.read_rows('SELECT * FROM checkpoints WHERE thread_id=?', (thread,)) == before
        assert app._storage.database.read_rows('SELECT * FROM writes WHERE thread_id=?', (thread,)) == writes
        result = await app.aresume_tool_approval(sid, ['approve'])
        assert result['messages'][-1].content == 'done'
        assert any(m.type == 'tool' and m.content == 'synthetic allowed result' for m in result['messages'])


@pytest.mark.parametrize('mutation', ['state', 'archive'])
@async_test
async def test_transaction_revalidates_before_deleting(monkeypatch, tmp_path, mutation, caplog):
    configure(monkeypatch, trigger_checkpoints=2)
    observations = []
    original = retention._commit
    def changed(storage, inventory, candidates, protected):
        before = storage.database.read_rows('SELECT * FROM checkpoints')
        with storage.database.connection:
            if mutation == 'state':
                storage.database.connection.execute("UPDATE naiyue_sessions SET state='running' WHERE session_id=?", (inventory['row']['session_id'],))
            else:
                storage.database.connection.execute('UPDATE naiyue_chat_sync SET revision=revision+1 WHERE session_id=?', (inventory['row']['session_id'],))
        try:
            return original(storage, inventory, candidates, protected)
        finally:
            observations.append((before, storage.database.read_rows('SELECT * FROM checkpoints')))
    monkeypatch.setattr(retention, '_commit', changed)
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'one')])
    assert observations and all(before == after for before, after in observations)
    assert ('session_changed' if mutation == 'state' else 'archive_changed') in caplog.text


@async_test
async def test_unknown_format_and_inspection_budget_skip_without_deletion(monkeypatch, tmp_path):
    configure(monkeypatch, enabled=False)
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'one')])
        row = app._storage.sessions.get(sid)
        saved = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        before = app._storage.database.read_rows('SELECT * FROM checkpoints')
        result = await retention.prune_after_finish(app._agent, app._storage, sid, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2, max_delete_bytes=1), snapshot=saved)
        assert result['reason'] == 'inspection_budget' and result['deleted_checkpoints'] == 0
        original = app._agent.checkpointer.aget_tuple
        async def old_format(config):
            value = await original(config)
            return value._replace(checkpoint={**value.checkpoint, 'v': 3})
        monkeypatch.setattr(app._agent.checkpointer, 'aget_tuple', old_format)
        result = await retention.prune_after_finish(app._agent, app._storage, sid, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2), snapshot=saved)
        assert result['reason'] == 'checkpoint_format_unsupported' and result['deleted_checkpoints'] == 0
        assert app._storage.database.read_rows('SELECT * FROM checkpoints') == before


@async_test
async def test_finished_recovery_keeps_original_source_and_pending_writes(monkeypatch, tmp_path):
    configure(monkeypatch, enabled=False)
    path = tmp_path / 'state.sqlite'
    async with build(monkeypatch, path) as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'one')])
        storage = app._storage
        row = storage.sessions.get(sid)
        original = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        source = original.config['configurable']['checkpoint_id']
        storage.runs.finish_run(sid, 'interrupted', source, '', 'interrupted')
    # Explicit recovery is a cold-session operation, not an in-memory rollback.
    async with build(monkeypatch, path) as app:
        storage = app._storage
        await app.arecover_session(sid, checkpoint_id=source, action='continue')
        saved = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        assert json.loads(saved.metadata['naiyue_recovery'])['source_checkpoint_id'] == source
        before = await app._agent.checkpointer.aget_tuple(original.config)
        row = storage.sessions.get(sid)
        result = await retention.prune_after_finish(app._agent, storage, sid, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2), snapshot=saved)
        after = await app._agent.checkpointer.aget_tuple(original.config)
        assert result['deleted_checkpoints'] > 0
        assert before.checkpoint == after.checkpoint and before.pending_writes == after.pending_writes
        assert storage.sessions.get(sid)['state'] == 'ready'


@async_test
async def test_pruned_compacted_root_keeps_child_archive_and_deletion_ownership(monkeypatch, tmp_path):
    from langchain_core.messages import HumanMessage, RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES
    from naiyue_test_agent.tools import create_subagent_tool
    from naiyue_test_agent.tools.subagent import SubAgentSpec
    configure(monkeypatch, trigger_checkpoints=2)
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'id': 'delegate', 'args': {'agent': 'worker', 'task': 'inspect'}}]),
        AIMessage(content='child report'), AIMessage(content='root done'),
        AIMessage(content='after summary'), AIMessage(content='last task')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model, [create_subagent_tool()]) as app:
        await app.aregister_subagent(SubAgentSpec(name='worker', description='Inspect.', system_prompt='Inspect.', tool_names=()))
        sid = await app.acreate_workspace_session('A', tmp_path)
        await app.arun(sid, [('user', 'delegate')])
        child, = await app.aget_chat_subagents(sid)
        child_thread = 'subagent:' + child['agent_id']
        thread = app._storage.sessions.get(sid)['thread_id']
        old_page = await app.aget_chat_page(sid, agent_id=child['agent_id'], limit=20, body_bytes=20000, preview_chars=100)
        assert old_page['records'] and len(checkpoints(app, child_thread)) > 0
        await app._agent.aupdate_state({'configurable': {'thread_id': thread}}, {'messages': [
            RemoveMessage(id=REMOVE_ALL_MESSAGES), HumanMessage(content='synthetic compacted context')]}, as_node='model')
        await app.arun(sid, [('user', 'continue')])
        await app.arun(sid, [('user', 'last')])
        remaining = [saved async for saved in app._agent.aget_state_history({'configurable': {'thread_id': thread}})]
        assert remaining and all(not any(getattr(m, 'tool_calls', []) for m in saved.values['messages']) for saved in remaining)
        assert await app.aget_chat_page(sid, agent_id=child['agent_id'], limit=20, body_bytes=20000, preview_chars=100) == old_page
        await app.adelete_session(sid)
        assert checkpoints(app, child_thread) == []
        assert app._storage.database.read_rows('SELECT * FROM writes WHERE thread_id=?', (child_thread,)) == []


@async_test
async def test_real_manual_compaction_keeps_full_archive_after_pruning_and_reopen(monkeypatch, tmp_path):
    from test_manual_compaction import compact_model
    configure(monkeypatch, trigger_checkpoints=2)
    model = compact_model()
    path = tmp_path / 'state.sqlite'
    async with build(monkeypatch, path, model) as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'old task')])
        await app.arun(sid, [('user', 'recent task')])
        before = app._storage.database.read_rows('SELECT message_id,body_json FROM naiyue_chat_messages WHERE session_id=? ORDER BY sequence', (sid,))
        await app.acompact_context(sid)
        compressed = await app.aget_messages(sid)
        assert compressed[0].additional_kwargs['naiyue_compaction_summary']
        after = app._storage.database.read_rows('SELECT message_id,body_json FROM naiyue_chat_messages WHERE session_id=? ORDER BY sequence', (sid,))
        assert after[:len(before)] == before
        assert len(checkpoints(app, app._storage.sessions.get(sid)['thread_id'])) < 10
    async with build(monkeypatch, path, model) as app:
        await app.arestore_session(sid)
        assert [m.id for m in await app.aget_messages(sid)] == [m.id for m in compressed]
        assert app._storage.database.read_rows('SELECT message_id,body_json FROM naiyue_chat_messages WHERE session_id=? ORDER BY sequence', (sid,)) == after
