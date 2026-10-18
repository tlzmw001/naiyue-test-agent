"""Native saver and threaded metadata preserve the existing persistence rules."""

import asyncio
import threading

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel


def factory(monkeypatch, path, model):
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    return open_async_persistent_application('unused', database_path=path,
        tools=[], system_prompt='', response_format=None)


def test_close_reopen_preserves_session_thread_and_history(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'sessions.sqlite'
        async with factory(monkeypatch, path, StreamingToolModel(replies=[AIMessage(content='first')])) as app:
            session = await app.acreate_session('A')
            await app.arun(session, [('user', 'one')])
            first = (await app.alist_sessions())[0]
            assert first['state'] == 'ready'
        async with factory(monkeypatch, path, StreamingToolModel(replies=[AIMessage(content='second')])) as app:
            assert await app.arestore_session(session) == session
            assert [m.content for m in await app.aget_messages(session)] == ['one', 'first']
            result = await app.arun(session, [('user', 'two')])
            second = (await app.alist_sessions())[0]
            assert [m.content for m in result['messages']] == ['one', 'first', 'two', 'second']
            assert first['thread_id'] == second['thread_id']
            assert first['run_id'] != second['run_id']
            assert first['checkpoint_id'] != second['checkpoint_id']
        assert path.stat().st_mode & 0o777 == 0o600
        with SessionStorage(path):
            pass
    asyncio.run(scenario())


def test_failed_run_keeps_confirmed_checkpoint_and_blocks_restart(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'sessions.sqlite'
        error = RuntimeError('PRIVATE')
        chosen = StreamingToolModel(replies=[AIMessage(content='first'), error])
        async with factory(monkeypatch, path, chosen) as app:
            session = await app.acreate_session('A')
            await app.arun(session, [('user', 'one')])
            first = (await app.alist_sessions())[0]
            with pytest.raises(RuntimeError) as caught:
                await app.arun(session, [('user', 'two')])
            assert caught.value is error
            failed = (await app.alist_sessions())[0]
            assert failed['state'] == 'failed' and failed['checkpoint_id'] == first['checkpoint_id']
            assert not app._sessions.describe_session(session)['busy']
        async with factory(monkeypatch, path, StreamingToolModel(replies=[])) as app:
            with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
                await app.arestore_session(session)
    asyncio.run(scenario())


def test_save_failure_never_publishes_completed(monkeypatch, tmp_path):
    async def scenario():
        async with factory(monkeypatch, tmp_path / 'sessions.sqlite',
                           StreamingToolModel(replies=[AIMessage(content='answer')])) as app:
            session = await app.acreate_session('A')
            events = []
            app.subscribe_events(events.append)

            def fail(*args, **kwargs):
                raise SessionPersistenceError('synthetic_save_failure')

            monkeypatch.setattr(app._storage, 'finish_run', fail)
            with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
                await app.arun(session, [('user', 'one')])
            assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'failed'
            assert not any(e.event_type == 'run_finished' and e.data.status == 'completed' for e in events)
            assert not app._sessions.describe_session(session)['busy']
            assert (await app.alist_sessions())[0]['state'] == 'running'
    asyncio.run(scenario())


def test_metadata_commit_wait_keeps_loop_responsive_and_busy(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        async with factory(monkeypatch, tmp_path / 'sessions.sqlite',
                           StreamingToolModel(replies=[AIMessage(content='answer')])) as app:
            session = await app.acreate_session('A')
            events = []
            app.subscribe_events(events.append)
            storage = app._storage
            original = storage.finish_run

            def commit(*args, **kwargs):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
                return original(*args, **kwargs)

            monkeypatch.setattr(storage, 'finish_run', commit)
            task = asyncio.create_task(app.arun(session, [('user', 'one')]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert not task.done() and app._sessions.describe_session(session)['busy']
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
                monkeypatch.setattr(app._catalog, 'aclose', close_models)
                raise original
        assert caught.value is original
        assert 'PRIVATE' not in caplog.text
        with SessionStorage(tmp_path / 'sessions.sqlite'):
            pass
    asyncio.run(scenario())


def test_cancel_during_storage_acquisition_releases_instance_lock(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.application import async_persistent
        path = tmp_path / 'sessions.sqlite'
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        instances = []
        original = async_persistent.SessionStorage

        def open_storage(*args):
            storage = original(*args)
            instances.append(storage)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return storage

        monkeypatch.setattr(async_persistent, 'SessionStorage', open_storage)

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
            assert instances[0]._closed
            with original(path):
                pass
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            for instance in instances:
                instance.close()
    asyncio.run(scenario())
