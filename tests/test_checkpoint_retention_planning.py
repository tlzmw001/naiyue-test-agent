"""Old large snapshots and provenance cannot permanently prevent safe reclamation."""

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from native_application_helpers import async_test
from test_application_run_events import ToolModel
from test_persistent_sessions import build
from test_checkpoint_retention import configure
from naiyue_test_agent.storage import checkpoint_retention as retention
from naiyue_test_agent.checkpoint_settings import CheckpointRetentionSettings
from naiyue_test_agent.application.errors import SessionPersistenceError
import pytest


@async_test
async def test_oversized_old_prefix_does_not_block_later_small_checkpoints(monkeypatch, tmp_path):
    configure(monkeypatch, enabled=False)
    model = ToolModel(responses=[AIMessage(content=text) for text in ('old', 'new', 'latest')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.sessions.create('A')
        await app.runs.start(sid, [('user', 'x' * 20000)])
        row = app.sessions._persistence.storage.sessions.get(sid)
        config = {'configurable': {'thread_id': row['thread_id']}}
        await app.runs._agent.aupdate_state(config, {'messages': [RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(content='compacted context')]}, as_node='model')
        await app.runs.start(sid, [('user', 'new')])
        await app.runs.start(sid, [('user', 'latest')])
        row = app.sessions._persistence.storage.sessions.get(sid)
        saved = await app.runs._agent.aget_state(config)
        results = [await retention.prune_after_finish(app.runs._agent, app.sessions._persistence.storage, sid, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2, max_delete_checkpoints=2, max_delete_bytes=16000),
            snapshot=saved) for _ in range(3)]
        assert sum(result['deleted_checkpoints'] for result in results) >= 4
        assert all(result['deleted_bytes'] <= 16000 for result in results)
        assert [m.content for m in await app.history.messages(sid)] == ['compacted context', 'new', 'new', 'latest', 'latest']


@async_test
async def test_historical_provenance_does_not_override_canonical_thread_ownership(monkeypatch, tmp_path):
    configure(monkeypatch, enabled=False)
    model = ToolModel(responses=[AIMessage(content=str(n)) for n in range(3)])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        a, b = await app.sessions.create('A'), await app.sessions.create('A')
        await app.runs.start(a, [('user', 'A')])
        await app.runs.start(b, [('user', 'B old')])
        await app.runs.start(b, [('user', 'B latest')])
        row = app.sessions._persistence.storage.sessions.get(a)
        # Historical provenance is not an executable boundary for B's latest task.
        with app.sessions._persistence.storage.database.connection:
            app.sessions._persistence.storage.database.connection.execute('''UPDATE naiyue_reply_boundaries SET source_thread_id=?
                WHERE session_id=? AND input_sequence=(SELECT MIN(input_sequence)
                    FROM naiyue_reply_boundaries WHERE session_id=?)''', (row['thread_id'], b, b))
        saved = await app.runs._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        result = await retention.prune_after_finish(app.runs._agent, app.sessions._persistence.storage, a, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2), snapshot=saved)
        assert result['deleted_checkpoints'] > 0


@async_test
async def test_shared_canonical_owner_prevents_deleting_either_sessions_data(monkeypatch, tmp_path):
    configure(monkeypatch, enabled=False)
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        a, b = await app.sessions.create('A'), await app.sessions.create('A')
        await app.runs.start(a, [('user', 'A')])
        row = app.sessions._persistence.storage.sessions.get(a)
        head = dict(app.sessions._persistence.storage.conversation.chat_heads(a)[0])
        with app.sessions._persistence.storage.database.connection:
            app.sessions._persistence.storage.database.connection.execute('INSERT INTO naiyue_chat_sync VALUES(?,?,?,?,?,?,?,?)',
                (b, head['thread_id'], head['checkpoint_ns'], head['agent_path'], head['checkpoint_id'],
                 head['revision'], head['history_version'], head['stopped']))
        before = app.sessions._persistence.storage.database.read_rows('SELECT * FROM checkpoints')
        saved = await app.runs._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        result = await retention.prune_after_finish(app.runs._agent, app.sessions._persistence.storage, a, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2), snapshot=saved)
        assert result['deleted_checkpoints'] == 0
        assert app.sessions._persistence.storage.database.read_rows('SELECT * FROM checkpoints') == before


@async_test
async def test_lost_archive_after_pruning_fails_instead_of_inventing_full_history(monkeypatch, tmp_path):
    configure(monkeypatch, trigger_checkpoints=2)
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        sid = await app.sessions.create('A')
        await app.runs.start(sid, [('user', 'one')])
        with app.sessions._persistence.storage.database.connection:
            app.sessions._persistence.storage.database.connection.execute('DELETE FROM naiyue_chat_messages WHERE session_id=?', (sid,))
            app.sessions._persistence.storage.database.connection.execute('DELETE FROM naiyue_chat_sync WHERE session_id=?', (sid,))
        with pytest.raises(SessionPersistenceError, match='chat_history_source_missing'):
            await app.sessions.prepare(sid)
        assert app.sessions._persistence.storage.conversation.chat_heads(sid) == []


@async_test
async def test_delta_channel_graph_is_never_pruned(monkeypatch, tmp_path):
    from langgraph.channels.delta import DeltaChannel
    configure(monkeypatch, enabled=False)
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        sid = await app.sessions.create('A')
        await app.runs.start(sid, [('user', 'one')])
        row = app.sessions._persistence.storage.sessions.get(sid)
        saved = await app.runs._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        before = app.sessions._persistence.storage.database.read_rows('SELECT * FROM checkpoints')
        app.runs._agent.channels['synthetic_delta'] = DeltaChannel(lambda seed, writes: writes)
        result = await retention.prune_after_finish(app.runs._agent, app.sessions._persistence.storage, sid, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2), snapshot=saved)
        assert result['reason'] == 'backend_or_channel_unsupported'
        assert app.sessions._persistence.storage.database.read_rows('SELECT * FROM checkpoints') == before


@async_test
async def test_ownership_is_rechecked_after_planning(monkeypatch, tmp_path, caplog):
    configure(monkeypatch, trigger_checkpoints=2)
    original = retention._commit
    observed = []
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        a, b = await app.sessions.create('A'), await app.sessions.create('A')
        def changed(storage, inventory, candidates, protected):
            head = inventory['heads'][0]
            with storage.database.connection:
                storage.database.connection.execute('INSERT INTO naiyue_chat_sync VALUES(?,?,?,?,?,?,?,?)',
                    (b, head['thread_id'], head['checkpoint_ns'], head['agent_path'], head['checkpoint_id'],
                     head['revision'], head['history_version'], head['stopped']))
            before = storage.database.read_rows('SELECT * FROM checkpoints')
            try:
                return original(storage, inventory, candidates, protected)
            finally:
                observed.append((before, storage.database.read_rows('SELECT * FROM checkpoints')))
        monkeypatch.setattr(retention, '_commit', changed)
        await app.runs.start(a, [('user', 'A')])
        assert app.sessions._persistence.storage.sessions.get(a)['state'] == 'ready'
    assert observed and all(before == after for before, after in observed)
    assert 'ownership_changed' in caplog.text


@async_test
async def test_required_save_failure_never_calls_optional_maintenance(monkeypatch, tmp_path):
    configure(monkeypatch, trigger_checkpoints=2)
    calls = []
    async def attempted(*args, **kwargs):
        calls.append(True)
    monkeypatch.setattr(retention, 'prune_after_finish', attempted)
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        sid = await app.sessions.create('A')
        def failed(*args, **kwargs):
            raise OSError('synthetic required save failure')
        monkeypatch.setattr(app.sessions._persistence.storage.runs, 'finish_run', failed)
        with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
            await app.runs.start(sid, [('user', 'A')])
        assert calls == [] and sid in app.sessions._persistence._blocked
