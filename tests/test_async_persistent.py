"""Native saver and threaded metadata preserve the existing persistence rules."""

import asyncio
import threading

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel


def factory(monkeypatch, path, model):
    from scripts.component_fixtures import open_component_application
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    return open_component_application('unused', database_path=path,
        tools=[], system_prompt='', response_format=None)


def test_close_reopen_preserves_session_thread_and_history(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'sessions.sqlite'
        async with factory(monkeypatch, path, StreamingToolModel(replies=[AIMessage(content='first')])) as app:
            session = await app.host.sessions.create('A')
            await app.host.runs.start(session, [('user', 'one')])
            first = (await app.host.sessions.list())[0]
            assert first['state'] == 'ready'
        async with factory(monkeypatch, path, StreamingToolModel(replies=[AIMessage(content='second')])) as app:
            assert await app.host.sessions.restore(session) == session
            assert [m.content for m in await app.host.history.messages(session)] == ['one', 'first']
            result = await app.host.runs.start(session, [('user', 'two')])
            second = (await app.host.sessions.list())[0]
            assert [m.content for m in result['messages']] == ['one', 'first', 'two', 'second']
            assert first['thread_id'] == second['thread_id']
            assert first['run_id'] != second['run_id']
            assert first['checkpoint_id'] != second['checkpoint_id']
        assert path.stat().st_mode & 0o777 == 0o600
        with ProjectStore(path):
            pass
    asyncio.run(scenario())


def test_failed_run_keeps_confirmed_checkpoint_and_blocks_restart(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'sessions.sqlite'
        error = RuntimeError('PRIVATE')
        chosen = StreamingToolModel(replies=[AIMessage(content='first'), error])
        async with factory(monkeypatch, path, chosen) as app:
            session = await app.host.sessions.create('A')
            await app.host.runs.start(session, [('user', 'one')])
            first = (await app.host.sessions.list())[0]
            with pytest.raises(RuntimeError) as caught:
                await app.host.runs.start(session, [('user', 'two')])
            assert caught.value is error
            failed = (await app.host.sessions.list())[0]
            assert failed['state'] == 'failed' and failed['checkpoint_id'] == first['checkpoint_id']
            assert not app.host.sessions._sessions.require(session).describe_session()['busy']
        async with factory(monkeypatch, path, StreamingToolModel(replies=[])) as app:
            with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
                await app.host.sessions.restore(session)
    asyncio.run(scenario())


def test_save_failure_never_publishes_completed(monkeypatch, tmp_path):
    async def scenario():
        async with factory(monkeypatch, tmp_path / 'sessions.sqlite',
                           StreamingToolModel(replies=[AIMessage(content='answer')])) as app:
            session = await app.host.sessions.create('A')
            events = []
            app.host.observability.subscribe(events.append)

            def fail(*args, **kwargs):
                raise SessionPersistenceError('synthetic_save_failure')

            monkeypatch.setattr(app.host.sessions._persistence.storage.runs, 'finish_run', fail)
            with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
                await app.host.runs.start(session, [('user', 'one')])
            assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'failed'
            assert not any(e.event_type == 'run_finished' and e.data.status == 'completed' for e in events)
            assert not app.host.sessions._sessions.require(session).describe_session()['busy']
            assert (await app.host.sessions.list())[0]['state'] == 'running'
    asyncio.run(scenario())


def test_metadata_commit_wait_keeps_loop_responsive_and_busy(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        async with factory(monkeypatch, tmp_path / 'sessions.sqlite',
                           StreamingToolModel(replies=[AIMessage(content='answer')])) as app:
            session = await app.host.sessions.create('A')
            events = []
            app.host.observability.subscribe(events.append)
            storage = app.host.sessions._persistence.storage
            original = storage.runs.finish_run

            def commit(*args, **kwargs):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
                return original(*args, **kwargs)

            monkeypatch.setattr(storage.runs, 'finish_run', commit)
            task = asyncio.create_task(app.host.runs.start(session, [('user', 'one')]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert not task.done() and app.host.sessions._sessions.require(session).describe_session()['busy']
                assert not any(e.event_type == 'run_finished' for e in events)
                release.set()
                await task
                assert events[-1].data.status == 'completed'
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_close_failure_does_not_replace_body_exception(monkeypatch, tmp_path, caplog):
    async def scenario():
        original, closing = RuntimeError('PRIMARY_PRIVATE'), ValueError('CLOSE_PRIVATE')
        with pytest.raises(RuntimeError) as caught:
            async with factory(monkeypatch, tmp_path / 'sessions.sqlite', StreamingToolModel(replies=[])) as app:
                async def close_models():
                    raise closing
                monkeypatch.setattr(app.host.sessions._sessions._catalog, 'aclose', close_models)
                raise original
        assert caught.value is original
        assert 'PRIVATE' not in caplog.text
        with ProjectStore(tmp_path / 'sessions.sqlite'):
            pass
    asyncio.run(scenario())


def test_cancel_during_storage_acquisition_releases_instance_lock(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.storage import resources as storage_resources
        path = tmp_path / 'sessions.sqlite'
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        instances = []
        original = storage_resources.ProjectStore

        def open_storage(*args, **kwargs):
            storage = original(*args, **kwargs)
            instances.append(storage)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return storage

        monkeypatch.setattr(storage_resources, 'ProjectStore', open_storage)

        async def open_application():
            async with factory(monkeypatch, path, StreamingToolModel(replies=[])):
                raise AssertionError('Cancelled acquisition must not enter the body')

        task = asyncio.create_task(open_application())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert instances[0].database._closed
            with original(path):
                pass
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            for instance in instances:
                instance.close()
    asyncio.run(scenario())
