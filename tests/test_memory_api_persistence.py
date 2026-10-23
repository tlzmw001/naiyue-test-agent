"""Memory API source locking, necessary save failures and resource settlement."""
import asyncio
import json
from threading import Event

import pytest
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel, call
from test_memory_tool import opened, feedback
from test_memory_lifecycle import proposal


def test_real_write_keeps_history_lock_from_source_save_through_mutation_and_reopen(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.application.sessions import session_persistence
        text = '以后测试使用.venv'
        model = StreamingToolModel(replies=[call('memory', dict(action='record', scope='workspace',
            proposal=proposal(), sources=[dict(current_user=True, quote=text)])), AIMessage(content='saved')])
        observed = []
        async with opened(monkeypatch, tmp_path, model) as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            persistence = app.sessions._persistence
            collect, query = session_persistence.collect_chat_updates, app.memory._access.query
            async def collect_sources(*args, **kwargs):
                if not kwargs:
                    observed.append(('source', persistence.history_lock(sid).locked()))
                return await collect(*args, **kwargs)
            def mutate(source, project, request, **kwargs):
                if request['action'] == 'record':
                    observed.append(('write', persistence.history_lock(sid).locked()))
                    rows = persistence.storage.database.read_rows('SELECT message_type FROM naiyue_chat_messages WHERE session_id=?', (sid,))
                    assert any(row['message_type'] == 'human' for row in rows)
                return query(source, project, request, **kwargs)
            monkeypatch.setattr(session_persistence, 'collect_chat_updates', collect_sources)
            monkeypatch.setattr(app.memory._access, 'query', mutate)
            result = await app.runs.start(sid, [('user', text)])
            saved = json.loads(feedback(result)[0].content)['item']
            assert observed == [('source', True), ('write', True)]
            assert saved['version'] == 1 and not persistence.history_lock(sid).locked()
        model = StreamingToolModel(replies=[call('memory', dict(action='read', scope='workspace',
            memory_id=saved['memory_id'], version=1)), AIMessage(content='restored')])
        async with opened(monkeypatch, tmp_path, model) as app:
            await app.sessions.restore(sid)
            result = await app.runs.start(sid, [('user', 'read saved')])
            assert feedback(result)[0].status == 'success' and '使用.venv' in feedback(result)[0].content
            owner = app.sessions._sessions.require(sid)
            assert owner.lifecycle._memory is app.memory and owner.subagents._memory is app.memory
    asyncio.run(scenario())


def test_source_save_failure_rejects_memory_write_but_preserves_root_finish(monkeypatch, tmp_path, caplog):
    async def scenario():
        from naiyue_test_agent.application.sessions import session_persistence
        text = '以后测试使用.venv'
        model = StreamingToolModel(replies=[call('memory', dict(action='record', scope='workspace',
            proposal=proposal(), sources=[dict(current_user=True, quote=text)])), AIMessage(content='write failed')])
        failures = []
        collect = session_persistence.collect_chat_updates
        async def fail_sources(*args, **kwargs):
            if not kwargs:
                failures.append(True)
                raise OSError('PRIVATE-source-body')
            return await collect(*args, **kwargs)
        monkeypatch.setattr(session_persistence, 'collect_chat_updates', fail_sources)
        async with opened(monkeypatch, tmp_path, model) as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            result = await app.runs.start(sid, [('user', text)])
            message, = feedback(result)
            assert failures == [True] and message.status == 'error' and 'memory_source_save_failed' in message.content
            assert 'PRIVATE-source-body' not in message.content + caplog.text
            assert result['messages'][-1].content == 'write failed'
            assert (await app.sessions.status(sid))['last_run_status'] == 'completed'
            assert app.sessions._persistence.storage.database.read_rows('SELECT memory_id FROM naiyue_memories') == []
            assert not app.sessions._persistence.history_lock(sid).locked()
    asyncio.run(scenario())


@pytest.mark.parametrize('cancel', [False, True])
def test_close_and_cancel_drain_started_memory_io_before_unlocking_or_closing(monkeypatch, tmp_path, cancel):
    async def scenario():
        text = '以后测试使用.venv'
        model = StreamingToolModel(replies=[call('memory', dict(action='record', scope='workspace',
            proposal=proposal(), sources=[dict(current_user=True, quote=text)])), AIMessage(content='saved')])
        entered, closing_started = asyncio.Event(), asyncio.Event()
        release, finished = Event(), Event()
        async with opened(monkeypatch, tmp_path, model) as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            access, persistence, loop = app.memory._access, app.sessions._persistence, asyncio.get_running_loop()
            query, close = access.query, access.close
            close_observations = []
            def held_query(source, project, request, **kwargs):
                if request['action'] == 'record':
                    loop.call_soon_threadsafe(entered.set)
                    assert release.wait(30), 'test did not release memory write'
                    result = query(source, project, request, **kwargs)
                    finished.set()
                    return result
                return query(source, project, request, **kwargs)
            def closed():
                close_observations.append((finished.is_set(), persistence.history_lock(sid).locked()))
                close()
            stop = app.sessions._sessions.stop_loaded_sessions
            def stopping():
                stop()
                closing_started.set()
            monkeypatch.setattr(access, 'query', held_query)
            monkeypatch.setattr(access, 'close', closed)
            monkeypatch.setattr(app.sessions._sessions, 'stop_loaded_sessions', stopping)
            running = asyncio.create_task(app.runs.start(sid, [('user', text)]))
            closing = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                if cancel:
                    running.cancel('cancel during memory write')
                closing = asyncio.create_task(app.aclose())
                await asyncio.wait_for(closing_started.wait(), 5)
                assert not closing.done() and persistence.history_lock(sid).locked()
                assert not close_observations and not finished.is_set()
                with pytest.raises(RuntimeError, match='owning event loop'):
                    await app.memory.query(scope=None, request={'action': 'list'})
                release.set()
                outcome, = await asyncio.gather(running, return_exceptions=True)
                await closing
                assert finished.is_set() and close_observations == [(True, False)]
                assert not app._lifetime._operations and app._lifetime._closed
                if cancel:
                    assert isinstance(outcome, asyncio.CancelledError)
                else:
                    assert not isinstance(outcome, BaseException)
            finally:
                release.set()
                await asyncio.gather(running, *([closing] if closing else []), return_exceptions=True)
    asyncio.run(scenario())
