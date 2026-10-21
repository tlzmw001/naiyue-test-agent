"""Old large snapshots and provenance cannot permanently prevent safe reclamation."""

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from native_application_helpers import async_test
from test_application_run_events import ToolModel
from test_persistent_sessions import build
from test_checkpoint_retention import configure
from naiyue_test_agent.application import checkpoint_retention as retention
from naiyue_test_agent.checkpoint_settings import CheckpointRetentionSettings
from naiyue_test_agent.application.errors import SessionPersistenceError
import pytest


@async_test
async def test_oversized_old_prefix_does_not_block_later_small_checkpoints(monkeypatch, tmp_path):
    configure(monkeypatch, enabled=False)
    model = ToolModel(responses=[AIMessage(content=text) for text in ('old', 'new', 'latest')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'x' * 20000)])
        row = app._storage.get(sid)
        config = {'configurable': {'thread_id': row['thread_id']}}
        await app._agent.aupdate_state(config, {'messages': [RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(content='compacted context')]}, as_node='model')
        await app.arun(sid, [('user', 'new')])
        await app.arun(sid, [('user', 'latest')])
        row = app._storage.get(sid)
        saved = await app._agent.aget_state(config)
        results = [await retention.prune_after_finish(app._agent, app._storage, sid, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2, max_delete_checkpoints=2, max_delete_bytes=16000),
            snapshot=saved) for _ in range(3)]
        assert sum(result['deleted_checkpoints'] for result in results) >= 4
        assert all(result['deleted_bytes'] <= 16000 for result in results)
        assert [m.content for m in await app.aget_messages(sid)] == ['compacted context', 'new', 'new', 'latest', 'latest']


@async_test
async def test_historical_provenance_does_not_override_canonical_thread_ownership(monkeypatch, tmp_path):
    configure(monkeypatch, enabled=False)
    model = ToolModel(responses=[AIMessage(content=str(n)) for n in range(3)])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        a, b = await app.acreate_session('A'), await app.acreate_session('A')
        await app.arun(a, [('user', 'A')])
        await app.arun(b, [('user', 'B old')])
        await app.arun(b, [('user', 'B latest')])
        row = app._storage.get(a)
        # Historical provenance is not an executable boundary for B's latest task.
        with app._storage.connection:
            app._storage.connection.execute('''UPDATE naiyue_reply_boundaries SET source_thread_id=?
                WHERE session_id=? AND input_sequence=(SELECT MIN(input_sequence)
                    FROM naiyue_reply_boundaries WHERE session_id=?)''', (row['thread_id'], b, b))
        saved = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        result = await retention.prune_after_finish(app._agent, app._storage, a, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2), snapshot=saved)
        assert result['deleted_checkpoints'] > 0


@async_test
async def test_shared_canonical_owner_prevents_deleting_either_sessions_data(monkeypatch, tmp_path):
    configure(monkeypatch, enabled=False)
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        a, b = await app.acreate_session('A'), await app.acreate_session('A')
        await app.arun(a, [('user', 'A')])
        row = app._storage.get(a)
        head = dict(app._storage.chat_heads(a)[0])
        with app._storage.connection:
            app._storage.connection.execute('INSERT INTO naiyue_chat_sync VALUES(?,?,?,?,?,?,?,?)',
                (b, head['thread_id'], head['checkpoint_ns'], head['agent_path'], head['checkpoint_id'],
                 head['revision'], head['history_version'], head['stopped']))
        before = app._storage._read('SELECT * FROM checkpoints')
        saved = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        result = await retention.prune_after_finish(app._agent, app._storage, a, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2), snapshot=saved)
        assert result['deleted_checkpoints'] == 0
        assert app._storage._read('SELECT * FROM checkpoints') == before


@async_test
async def test_lost_archive_after_pruning_fails_instead_of_inventing_full_history(monkeypatch, tmp_path):
    configure(monkeypatch, trigger_checkpoints=2)
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'one')])
        with app._storage.connection:
            app._storage.connection.execute('DELETE FROM naiyue_chat_messages WHERE session_id=?', (sid,))
            app._storage.connection.execute('DELETE FROM naiyue_chat_sync WHERE session_id=?', (sid,))
        with pytest.raises(SessionPersistenceError, match='chat_history_source_missing'):
            await app.aprepare_session(sid)
        assert app._storage.chat_heads(sid) == []


@async_test
async def test_delta_channel_graph_is_never_pruned(monkeypatch, tmp_path):
    from langgraph.channels.delta import DeltaChannel
    configure(monkeypatch, enabled=False)
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'one')])
        row = app._storage.get(sid)
        saved = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        before = app._storage._read('SELECT * FROM checkpoints')
        app._agent.channels['synthetic_delta'] = DeltaChannel(lambda seed, writes: writes)
        result = await retention.prune_after_finish(app._agent, app._storage, sid, row['run_id'],
            CheckpointRetentionSettings(trigger_checkpoints=2), snapshot=saved)
        assert result['reason'] == 'backend_or_channel_unsupported'
        assert app._storage._read('SELECT * FROM checkpoints') == before


@async_test
async def test_ownership_is_rechecked_after_planning(monkeypatch, tmp_path, caplog):
    configure(monkeypatch, trigger_checkpoints=2)
    original = retention._commit
    observed = []
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        a, b = await app.acreate_session('A'), await app.acreate_session('A')
        def changed(storage, inventory, candidates, protected):
            head = inventory['heads'][0]
            with storage.connection:
                storage.connection.execute('INSERT INTO naiyue_chat_sync VALUES(?,?,?,?,?,?,?,?)',
                    (b, head['thread_id'], head['checkpoint_ns'], head['agent_path'], head['checkpoint_id'],
                     head['revision'], head['history_version'], head['stopped']))
            before = storage._read('SELECT * FROM checkpoints')
            try:
                return original(storage, inventory, candidates, protected)
            finally:
                observed.append((before, storage._read('SELECT * FROM checkpoints')))
        monkeypatch.setattr(retention, '_commit', changed)
        await app.arun(a, [('user', 'A')])
        assert app._storage.get(a)['state'] == 'ready'
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
        sid = await app.acreate_session('A')
        def failed(*args, **kwargs):
            raise OSError('synthetic required save failure')
        monkeypatch.setattr(app._storage, 'finish_run', failed)
        with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
            await app.arun(sid, [('user', 'A')])
        assert calls == [] and sid in app._persistence._blocked
