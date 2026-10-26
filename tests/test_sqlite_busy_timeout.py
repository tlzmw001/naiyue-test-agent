"""One explicit lock-wait budget for both project SQLite connections."""
import asyncio
import sqlite3

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from pydantic import ValidationError

from naiyue_test_agent.config_paths import StorageSettings
from naiyue_test_agent.settings import AppSettings, load_settings
from naiyue_test_agent.storage.resources import open_storage


def test_timeout_defaults_and_toml_override(tmp_path):
    assert StorageSettings().busy_timeout_ms == 5000
    path = tmp_path / 'settings.toml'
    path.write_text('[storage]\nbusy_timeout_ms=1370\n')
    assert load_settings(path).storage.busy_timeout_ms == 1370


@pytest.mark.parametrize('value', [-1, 30001, True, 1.5, '1000', None])
def test_invalid_timeout_is_rejected(value):
    with pytest.raises(ValidationError):
        StorageSettings(busy_timeout_ms=value)


@pytest.mark.parametrize('memory', [True, False])
def test_both_connections_receive_the_same_configured_timeout(tmp_path, memory):
    async def scenario():
        settings = StorageSettings(busy_timeout_ms=1370)
        async with open_storage(None if memory else tmp_path / 'sessions.sqlite', storage_settings=settings) as resources:
            sync = resources.store.database.read_rows('PRAGMA busy_timeout')[0]['timeout']
            async with resources.checkpointer.conn.execute('PRAGMA busy_timeout') as cursor:
                asynchronous = (await cursor.fetchone())[0]
            assert sync == asynchronous == 1370
    asyncio.run(scenario())


def test_zero_timeout_fails_busy_checkpoint_and_connection_recovers(tmp_path):
    async def scenario():
        async with open_storage(tmp_path / 'sessions.sqlite',
                storage_settings=StorageSettings(busy_timeout_ms=0)) as resources:
            config = {'configurable': {'thread_id': 'B', 'checkpoint_ns': ''}}
            checkpoint = empty_checkpoint()
            with resources.store.database.transaction(write=True):
                with pytest.raises(sqlite3.OperationalError) as caught:
                    await resources.checkpointer.aput(config, checkpoint, {}, {})
                assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
            saved = await resources.checkpointer.aput(config, checkpoint, {}, {})
            assert (await resources.checkpointer.aget_tuple(saved)).checkpoint['id'] == checkpoint['id']
    asyncio.run(scenario())


def test_service_hosts_and_cold_reads_receive_storage_timeout(monkeypatch, tmp_path):
    from naiyue_test_agent import application
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.storage.project_store import ProjectStore
    from test_async_application import StreamingToolModel
    async def scenario():
        monkeypatch.setattr(application, 'load_model_catalog',
            lambda _: ModelCatalog('A', {'A': StreamingToolModel(replies=[])}))
        settings = AppSettings(storage=StorageSettings(data_dir=tmp_path / 'data', busy_timeout_ms=1370))
        async with open_service_application('unused', tools=(), system_prompt='', response_format=None,
                                            settings=settings) as app:
            seen = []
            original = ProjectStore._assemble
            def observe(self, database):
                seen.append(database.read_rows('PRAGMA busy_timeout')[0]['timeout'])
                return original(self, database)
            monkeypatch.setattr(ProjectStore, '_assemble', observe)
            root = tmp_path / 'code'
            root.mkdir()
            project = await app.projects.add_local(root, trusted=True, operation_id='add')
            assert await app.projects.list_sessions(project.project_id) == []
            hosts = [await app.projects.open(project.project_id), await app.chats.open(),
                     await app.temporary.open('temp-timeout')]
            for host in hosts:
                async with host.runs._agent.checkpointer.conn.execute('PRAGMA busy_timeout') as cursor:
                    assert (await cursor.fetchone())[0] == 1370
            assert len(seen) == 5 and set(seen) == {1370}
    asyncio.run(scenario())


def test_other_session_streams_and_saves_during_real_checkpoint_prune(monkeypatch, tmp_path, caplog):
    from threading import Event
    from test_cross_session_concurrency import concurrent_app, gated
    async def scenario():
        settings = AppSettings(storage=StorageSettings(busy_timeout_ms=5000),
                               checkpoint_retention={'trigger_checkpoints': 2})
        async with concurrent_app(monkeypatch, tmp_path / 'sessions.sqlite', settings=settings) as (app, model):
            host = app.host
            a, b = await host.sessions.create('A'), await host.sessions.create('A')
            for _ in range(2):
                await host.runs.start(a, [('user', a)])
            store = host.sessions._persistence.storage
            saver = host.runs._agent.checkpointer
            thread = store.sessions.get(b)['thread_id']
            model_entered, model_release = gated(model, b)
            prune_entered, checkpoint_waiting = asyncio.Event(), asyncio.Event()
            release_prune = Event()
            loop = asyncio.get_running_loop()
            pause_once = Event()
            def pause():
                if not pause_once.is_set():
                    pause_once.set()
                    loop.call_soon_threadsafe(prune_entered.set)
                    assert release_prune.wait(10), 'test must release the prune transaction'
                return 0
            store.database.connection.create_function('test_pause_prune', 0, pause)
            store.database.connection.execute('''CREATE TEMP TRIGGER test_hold_prune BEFORE DELETE ON checkpoints
                BEGIN SELECT test_pause_prune(); END''')
            def trace(statement):
                # LangGraph persists pending writes before the next checkpoint.
                if (pause_once.is_set() and statement.startswith('INSERT') and thread in statement
                        and (' INTO checkpoints ' in statement or ' INTO writes ' in statement)):
                    loop.call_soon_threadsafe(checkpoint_waiting.set)
            await saver.conn.set_trace_callback(trace)
            events = []
            async def stream_b():
                async for event in host.runs.stream(b, [('user', b)]):
                    events.append(event)
            other = asyncio.create_task(stream_b())
            running = None
            try:
                await asyncio.wait_for(model_entered.wait(), 5)
                running = asyncio.create_task(host.runs.start(a, [('user', 'prune now')]))
                await asyncio.wait_for(prune_entered.wait(), 5)
                model_release.set()
                await asyncio.wait_for(checkpoint_waiting.wait(), 5)
                assert not other.done() and not release_prune.is_set()
                release_prune.set()
                await asyncio.wait_for(asyncio.gather(running, other), 10)
                assert events[0].event_type == 'run_started'
                assert any(event.event_type == 'message_delta' for event in events)
                assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'completed'
                assert store.sessions.get(b)['state'] == 'ready'
                assert (await host.history.messages(b))[-1].content == 'done:' + b
                assert 'session_persistence_finalize_failed' not in caplog.text
                assert 'checkpoint_retention_failed' not in caplog.text
            finally:
                release_prune.set()
                model_release.set()
                await asyncio.gather(*(t for t in (running, other) if t is not None), return_exceptions=True)
                await saver.conn.set_trace_callback(None)
    asyncio.run(scenario())
