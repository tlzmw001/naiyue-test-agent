"""Bounded ancestry reads, ownership, migrations and archive deletion."""

from legacy_store_schema import remove_reply_schema

import sqlite3
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from native_application_helpers import async_test
from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
from test_chat_history import rows
from test_persistent_sessions import build


def create_row(storage, sid='s'):
    storage.create(sid, {'thread_id': sid, 'model_id': 'A', 'workspace_root': None,
                        'active_tool_names': (), 'permission_mode': 'Default'})


def snapshot(identity, parent, messages, thread='s'):
    config = {'configurable': {'thread_id': thread, 'checkpoint_ns': '', 'checkpoint_id': identity}}
    return SimpleNamespace(config=config, parent_config={'configurable': {
        **config['configurable'], 'checkpoint_id': parent}} if parent else None, values={'messages': messages})


class SavedGraph:
    def __init__(self, latest, versions):
        self.latest, self.versions, self.reads = latest, versions, []

    async def aget_state(self, config):
        return self.latest

    async def aget_state_history(self, config, *, before, limit):
        assert limit is not None and limit > 0
        self.reads.append((before, limit))
        identity = before['configurable']['checkpoint_id'] if before else None
        start = next(i + 1 for i, row in enumerate(self.versions)
                     if row.config['configurable']['checkpoint_id'] == identity) if before else 0
        for row in self.versions[start:start + limit]:
            yield row


@async_test
async def test_paged_collection_uses_only_current_ancestry_and_stops_at_cursor(tmp_path):
    from naiyue_test_agent.application.chat_history import collect_chat_updates
    chain = [snapshot(str(i), str(i-1) if i else None, [HumanMessage(id=f'h{i}', content=f'v{i}')]) for i in range(131)]
    sibling = snapshot('unrelated', '99', [AIMessage(id='other-branch', content='must not archive')])
    graph = SavedGraph(chain[-1], [*reversed(chain[111:]), sibling, *reversed(chain[:111])])
    with SessionStorage(tmp_path / 'state.sqlite') as storage:
        create_row(storage)
        updates = await collect_chat_updates(graph, storage, 's')
        storage.finish_run('s', 'ready', '130', '', 'completed', updates)
        assert [row['message_id'] for row in rows(storage, 's')] == [f'h{i}' for i in range(131)]
        assert len(graph.reads) == 3 and all(limit == 64 for _, limit in graph.reads)
        later = snapshot('131', '130', [HumanMessage(id='new', content='later')])
        graph = SavedGraph(later, [later, *reversed(chain)])
        updates = await collect_chat_updates(graph, storage, 's')
        assert len(graph.reads) == 1 and [m['message_id'] for m in updates[0].messages] == ['new']
        storage.finish_run('s', 'ready', '131', '', 'completed', updates)
        assert len(rows(storage, 's')) == 132


@async_test
async def test_same_checkpoint_collects_new_pending_result_without_history_read(tmp_path):
    from naiyue_test_agent.application.chat_history import collect_chat_updates
    original = snapshot('cp', None, [HumanMessage(id='h', content='request')])
    graph = SavedGraph(original, [original])
    with SessionStorage(tmp_path / 'state.sqlite') as storage:
        create_row(storage)
        storage.finish_run('s', 'failed', None, '', 'failed', await collect_chat_updates(graph, storage, 's'))
        graph.reads.clear()
        graph.latest = snapshot('cp', None, [*original.values['messages'], AIMessage(id='pending', content='saved')])
        storage.finish_run('s', 'failed', None, '', 'failed', await collect_chat_updates(graph, storage, 's'))
        assert [r['message_id'] for r in rows(storage, 's')] == ['h', 'pending'] and graph.reads == []


@pytest.mark.parametrize('fault', ['missing_parent', 'missing_id', 'bad_body', 'duplicate_id', 'wrong_thread'])
@async_test
async def test_unreadable_or_ambiguous_source_never_advances_archive(tmp_path, fault):
    from naiyue_test_agent.application.chat_history import collect_chat_updates
    messages = [HumanMessage(id='h', content='request')]
    parent = 'absent' if fault == 'missing_parent' else None
    if fault == 'missing_id': messages[0].id = None
    if fault == 'bad_body': messages[0].additional_kwargs['invalid'] = object()
    if fault == 'duplicate_id': messages.append(AIMessage(id='h', content='ambiguous'))
    latest = snapshot('cp', parent, messages, 'other' if fault == 'wrong_thread' else 's')
    graph = SavedGraph(latest, [latest])
    with SessionStorage(tmp_path / 'state.sqlite') as storage:
        create_row(storage)
        with pytest.raises(SessionPersistenceError, match='chat_history_'):
            await collect_chat_updates(graph, storage, 's')
        assert storage.chat_heads('s') == [] and rows(storage, 's') == []
        assert storage.get('s')['state'] == 'ready'


def test_schema_six_migration_preserves_rows_and_does_not_backfill(tmp_path):
    path = tmp_path / 'six.sqlite'
    with SessionStorage(path) as storage:
        create_row(storage)
    with sqlite3.connect(path) as connection:
        remove_reply_schema(connection)
        connection.executescript('''DROP TABLE naiyue_model_invocations; DROP TABLE naiyue_model_usage_runs;
            DROP TABLE naiyue_chat_messages; DROP TABLE naiyue_chat_sync;
            UPDATE naiyue_store_info SET version=6;''')
    with SessionStorage(path) as storage:
        assert storage.get('s')['thread_id'] == 's'
        assert storage.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 13
        assert storage.chat_heads('s') == [] and rows(storage, 's') == []


def test_chat_migration_is_atomic_on_ddl_failure(monkeypatch, tmp_path):
    path = tmp_path / 'six.sqlite'
    with SessionStorage(path): pass
    with sqlite3.connect(path) as connection:
        remove_reply_schema(connection)
        connection.executescript('''DROP TABLE naiyue_model_invocations; DROP TABLE naiyue_model_usage_runs;
            DROP TABLE naiyue_chat_messages; DROP TABLE naiyue_chat_sync;
            UPDATE naiyue_store_info SET version=6;''')
    connect = sqlite3.connect
    def denied(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'naiyue_chat_messages' else sqlite3.SQLITE_OK)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', denied)
        with pytest.raises(SessionPersistenceError, match='migration_failed') as caught:
            SessionStorage(path)
    from naiyue_test_agent.storage_backup import validate_backup
    assert (caught.value.source_schema, caught.value.current_schema, caught.value.target_schema) == (6, 6, 13)
    assert validate_backup(path, caught.value.backup_id)['source_schema'] == 6
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 6
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'naiyue_chat_%'").fetchall() == []


@async_test
async def test_delete_clears_archive_and_progress_only_for_selected_session(monkeypatch, tmp_path):
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        first, second = await app.acreate_session('A'), await app.acreate_session('A')
        await app.arun(first, [('user', 'one')])
        await app.arun(second, [('user', 'two')])
        storage = app._storage
        saved = rows(storage, second)
        await app.adelete_session(first)
        assert rows(storage, first) == [] and storage.chat_heads(first) == []
        assert rows(storage, second) == saved and storage.chat_heads(second)


@async_test
async def test_actual_saver_backfill_pages_after_saved_archive_is_absent(monkeypatch, tmp_path):
    from naiyue_test_agent.application.chat_history import collect_chat_updates
    from test_application_run_events import ToolModel
    from naiyue_test_agent import application
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.checkpoint_settings import CheckpointRetentionSettings
    # Generate the pre-retention schema6 fixture; all backfill assertions remain.
    monkeypatch.setattr(application, 'load_settings', lambda: AppSettings(
        checkpoint_retention=CheckpointRetentionSettings(enabled=False)))
    model = ToolModel(responses=[AIMessage(id=f'answer-{index}', content=f'answer {index}') for index in range(12)])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.acreate_session('A')
        for index in range(12):
            await app.arun(sid, [('user', f'request {index}')])
        storage = app._storage
        original = rows(storage, sid)
        # A schema6 session starts with saved checkpoints but no archive/head.
        with storage.connection:
            storage.connection.execute('DELETE FROM naiyue_chat_messages WHERE session_id=?', (sid,))
            storage.connection.execute('DELETE FROM naiyue_chat_sync WHERE session_id=?', (sid,))
        history = app._agent.aget_state_history
        reads = []
        async def counted(config, **kwargs):
            reads.append(kwargs)
            async for item in history(config, **kwargs):
                yield item
        with monkeypatch.context() as patch:
            patch.setattr(app._agent, 'aget_state_history', counted)
            updates = await collect_chat_updates(app._agent, storage, sid)
        storage.finish_run(sid, 'ready', storage.get(sid)['checkpoint_id'], '', 'completed', updates)
        assert len(reads) >= 2 and all(r['limit'] == 64 for r in reads)
        assert reads[0]['before'] is None and all(r['before'] is not None for r in reads[1:])
        actual = rows(storage, sid)
        assert actual == original, [(new['sequence'], {key: (old[key], new[key])
            for key in old if old[key] != new[key] and key != 'body_json'}) for old, new in zip(original, actual) if old != new]
