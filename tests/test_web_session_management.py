"""Presentation changes never execute the agent; deletion removes only owned history."""

from legacy_store_schema import remove_reply_schema

import asyncio
import sqlite3
from threading import Event

import pytest

from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel
from test_web_api import request, web_client


def test_names_pins_order_are_validated_and_persist_without_model_calls(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            ids = [(await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id'] for _ in range(3)]
            endpoint = f'/api/sessions/{ids[0]}/presentation'
            assert (await client.patch(endpoint, json={'title': '  接口测试  ', 'pinned': True})).status_code == 200
            rows = (await client.get('/api/sessions')).json()
            assert rows[0]['session_id'] == ids[0] and rows[0]['title'] == '接口测试' and rows[0]['pinned'] is True
            for body in ({'title': ''}, {'title': '   '}, {'title': 'x' * 121}, {'title': 'bad\x00name'},
                         {'pinned': 1}, {}, {'title': 'ok', 'extra': True}):
                assert (await client.patch(endpoint, json=body)).status_code == 422
            assert (await client.patch(endpoint, json={'pinned': False}, headers={'Authorization': ''})).status_code == 401
            assert (await client.delete(f'/api/sessions/{ids[0]}', headers={'Origin': 'https://evil.example'})).status_code == 403
            ordered = [ids[0], ids[1], ids[2]]
            assert (await client.put('/api/session-order', json={'session_ids': ordered})).status_code == 200
            assert [row['session_id'] for row in (await client.get('/api/sessions')).json()] == ordered
            for invalid in ([ids[0]], ids + [ids[0]], [*ids[:2], 'foreign'], list(reversed(ids))):
                assert (await client.put('/api/session-order', json={'session_ids': invalid})).status_code in (409, 422)
            assert (await client.get('/api/sessions')).json()[0]['title'] == '接口测试'
            assert model.seen == []
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            rows = (await client.get('/api/sessions')).json()
            assert [row['session_id'] for row in rows] == ordered
            assert rows[0]['title'] == '接口测试' and rows[0]['pinned'] is True
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            assert [row['session_id'] for row in (await client.get('/api/sessions')).json()] == [ids[0], sid, ids[1], ids[2]]
    asyncio.run(scenario())


def test_other_workspace_and_stale_order_cannot_change_sessions(monkeypatch, tmp_path):
    async def scenario():
        root, other = tmp_path / 'one', tmp_path / 'two'
        root.mkdir(); other.mkdir()
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), workspace_root=root) as (client, web):
            foreign = await web.state.service.application.sessions.create_workspace('A', other)
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            assert (await client.patch(f'/api/sessions/{foreign}/presentation', json={'title': 'hidden'})).status_code == 404
            assert (await client.delete(f'/api/sessions/{foreign}')).status_code == 404
            assert (await client.put('/api/session-order', json={'session_ids': [foreign, sid]})).status_code == 409
            assert (await client.put('/api/session-order', json={'session_ids': [sid]})).status_code == 200
            new = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            response = await client.put('/api/session-order', json={'session_ids': [sid]})
            assert response.json() == {'error': {'code': 'stale_session_order'}}
            assert [row['session_id'] for row in (await client.get('/api/sessions')).json()] == [new, sid]
    asyncio.run(scenario())


def test_delete_removes_all_checkpoints_and_delivery_without_touching_other_session(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='saved')])
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid, kept = [(await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id'] for _ in range(2)]
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('hello'))).json()['run_id']
            await web.state.delivery.wait(run)
            store = web.state.service.application.sessions._persistence.storage
            thread = store.sessions.get(sid)['thread_id']
            assert store.database.connection.execute('SELECT count(*) FROM checkpoints WHERE thread_id=?', (thread,)).fetchone()[0] > 0
            response = await client.delete(f'/api/sessions/{sid}')
            assert response.status_code == 200 and response.json() == {'session_id': sid}
            assert [row['session_id'] for row in (await client.get('/api/sessions')).json()] == [kept]
            assert (await client.get(f'/api/sessions/{sid}')).status_code == 404
            assert (await client.get(f'/api/runs/{run}')).status_code == 404
            assert not web.state.service.application.sessions.is_loaded(sid)
            assert store.database.connection.execute('SELECT count(*) FROM checkpoints WHERE thread_id=?', (thread,)).fetchone()[0] == 0
            assert store.database.connection.execute('SELECT count(*) FROM writes WHERE thread_id=?', (thread,)).fetchone()[0] == 0
            assert not any(key[0] == sid for key in web.state.delivery.receipts)
            assert len(model.seen) == 1
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            assert [row['session_id'] for row in (await client.get('/api/sessions')).json()] == [kept]
    asyncio.run(scenario())


def test_deletion_is_blocked_during_a_run_and_cannot_replay_its_cached_receipt(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[], entered=entered, release=release)
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('wait'))).json()['run_id']
            await asyncio.wait_for(entered.wait(), 5)
            try:
                response = await client.delete(f'/api/sessions/{sid}')
                assert response.json() == {'error': {'code': 'session_busy'}}
                assert (await client.get('/api/sessions')).json()[0]['session_id'] == sid
            finally:
                await client.post(f'/api/runs/{run}/controls', json={**request(''), 'kind': 'stop', 'text': None})
                release.set()
                await web.state.delivery.wait(run)
            assert (await client.delete(f'/api/sessions/{sid}')).status_code == 200
            assert (await client.get(f'/api/runs/{run}/events')).status_code == 404
    asyncio.run(scenario())


def test_delete_includes_child_history_removed_from_latest_parent_checkpoint(monkeypatch, tmp_path):
    from langchain_core.messages import HumanMessage, RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES
    from naiyue_test_agent.tools.subagent import SubAgentSpec
    from test_async_application import call

    async def scenario():
        model = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'inspect'}, 'outer'),
            AIMessage(content='child report'), AIMessage(content='root done')])
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            app = web.state.service.application
            await app.subagents.register(SubAgentSpec(name='tester', description='Inspect a task.', system_prompt='Inspect.', tool_names=()))
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            await client.patch(f'/api/sessions/{sid}/tools', json={'active_tools': ['subagent']})
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('delegate'))).json()['run_id']
            await web.state.delivery.wait(run)
            child, = await app.history.subagents(sid)
            child_thread = f'subagent:{child["agent_id"]}'
            store = app.sessions._persistence.storage
            assert store.database.connection.execute('SELECT count(*) FROM checkpoints WHERE thread_id=?', (child_thread,)).fetchone()[0] > 0
            thread = store.sessions.get(sid)['thread_id']
            await app.runs._agent.aupdate_state({'configurable': {'thread_id': thread}}, {'messages': [
                RemoveMessage(id=REMOVE_ALL_MESSAGES), HumanMessage(content='summary'), AIMessage(content='compacted')]}, as_node='model')
            assert not any(getattr(message, 'tool_calls', []) for message in await app.history.messages(sid))
            assert await app.history.subagents(sid) == [child]
            # The prompt no longer references the child; the persisted registry still owns it.
            for owner in app.sessions._sessions._sessions.values():
                assert owner._runtime.run is None
            assert (await client.delete(f'/api/sessions/{sid}')).status_code == 200
            assert store.database.connection.execute('SELECT count(*) FROM checkpoints WHERE thread_id=?', (child_thread,)).fetchone()[0] == 0
            assert store.database.connection.execute('SELECT count(*) FROM writes WHERE thread_id=?', (child_thread,)).fetchone()[0] == 0
            assert store.subagents.get_subagent_references(sid) == {}
            assert len(model.seen) == 3
    asyncio.run(scenario())


def test_schema_five_migration_keeps_recency_then_persists_manual_order(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore

    path = tmp_path / 'five.sqlite'
    with ProjectStore(path) as store:
        for sid in ('old', 'recent'):
            store.sessions.create(sid, {'thread_id': sid, 'model_id': 'A', 'workspace_root': None,
                               'active_tool_names': (), 'permission_mode': 'Default'})
        store.sessions.select_thinking('recent', 'high')
    with sqlite3.connect(path) as connection:
        remove_reply_schema(connection)
        connection.executescript('''DROP TABLE naiyue_model_invocations; DROP TABLE naiyue_model_usage_runs;
            DROP TABLE naiyue_chat_messages; DROP TABLE naiyue_chat_sync;
            ALTER TABLE naiyue_sessions DROP COLUMN title;
            ALTER TABLE naiyue_sessions DROP COLUMN pinned;
            ALTER TABLE naiyue_sessions DROP COLUMN sort_order;
            UPDATE naiyue_store_info SET version=5;''')
    with ProjectStore(path) as store:
        assert [row['session_id'] for row in store.sessions.list_sessions()] == ['recent', 'old']
        assert store.sessions.get('recent')['thinking_level'] == 'high'
        store.sessions.reorder(None, ['old', 'recent'])
        store.sessions.select_model('recent', 'B')
        assert [row['session_id'] for row in store.sessions.list_sessions()] == ['old', 'recent']


def test_restore_holds_ownership_until_session_install_before_deletion(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='saved')])
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('hello'))).json()['run_id']
            await web.state.delivery.wait(run)
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            app = web.state.service.application
            entered, release = asyncio.Event(), asyncio.Event()
            original = app.runs._agent.aget_state

            async def pause(*args, **kwargs):
                snapshot = await original(*args, **kwargs)
                entered.set()
                await release.wait()
                return snapshot

            monkeypatch.setattr(app.runs._agent, 'aget_state', pause)
            reading = asyncio.create_task(client.post(f'/api/sessions/{sid}/prepare'))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                deleting = await client.delete(f'/api/sessions/{sid}')
                assert deleting.status_code == 409
                assert len((await client.get('/api/sessions')).json()) == 1
            finally:
                release.set()
                await reading
            assert (await client.delete(f'/api/sessions/{sid}')).status_code == 200
            assert not app.sessions.is_loaded(sid)
    asyncio.run(scenario())


def test_cancelled_delete_still_revokes_completed_run_cache(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='private history')])) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('hello'))).json()['run_id']
            await web.state.delivery.wait(run)
            store = web.state.service.application.sessions._persistence.storage
            loop, committed, release = asyncio.get_running_loop(), asyncio.Event(), Event()
            original = store.runs.delete

            def paused(*args):
                original(*args)
                loop.call_soon_threadsafe(committed.set)
                assert release.wait(5)

            monkeypatch.setattr(store.runs, 'delete', paused)
            deleting = asyncio.create_task(client.delete(f'/api/sessions/{sid}'))
            try:
                await asyncio.wait_for(committed.wait(), 5)
                deleting.cancel()
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await deleting
            assert (await client.get(f'/api/sessions/{sid}')).status_code == 404
            assert (await client.get(f'/api/runs/{run}')).status_code == 404
            assert sid not in web.state.delivery.latest
    asyncio.run(scenario())


def test_delete_cannot_replay_control_text_from_an_evicted_run(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='saved'), AIMessage(content='saved')],
                                   entered=entered, release=release)
        async with web_client(monkeypatch, tmp_path, model, retained_runs=1) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            start = request('wait')
            run = (await client.post(f'/api/sessions/{sid}/runs', json=start)).json()['run_id']
            await asyncio.wait_for(entered.wait(), 5)
            stop = {'request_id': request('')['request_id'], 'kind': 'stop'}
            try:
                queued = await client.post(f'/api/runs/{run}/controls',
                    json={**request('synthetic pending text'), 'kind': 'follow_up'})
                assert queued.status_code == 202
                stopping = await client.post(f'/api/runs/{run}/controls', json=stop)
                assert stopping.status_code == 202
                assert stopping.json()['retained'] == ['synthetic pending text']
                assert (await client.post(f'/api/runs/{run}/controls', json=stop)).json() == stopping.json()
            finally:
                release.set()
                await web.state.delivery.wait(run)
            for _ in range(2):
                current = (await client.post(f'/api/sessions/{sid}/runs', json=request('next'))).json()['run_id']
                await web.state.delivery.wait(current)
            assert run not in web.state.delivery.runs
            repeat_start = await client.post(f'/api/sessions/{sid}/runs', json=start)
            assert repeat_start.status_code == 202 and repeat_start.json()['run_id'] == run
            assert len(model.seen) == 3  # Eviction must not replay a session-scoped start receipt.
            assert (await client.delete(f'/api/sessions/{sid}')).status_code == 200
            assert (await client.get(f'/api/sessions/{sid}')).status_code == 404
            replay = await client.post(f'/api/runs/{run}/controls', json=stop)
            assert replay.status_code == 404
            assert replay.json() == {'error': {'code': 'run_not_found'}}
            assert (run, stop['request_id']) not in web.state.delivery.receipts
    asyncio.run(scenario())


def test_delete_failure_rolls_back_metadata_and_history(monkeypatch, tmp_path, caplog):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='kept')])) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('hello'))).json()['run_id']
            await web.state.delivery.wait(run)
            store = web.state.service.application.sessions._persistence.storage
            store.database.connection.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON naiyue_sessions BEGIN SELECT RAISE(ABORT,'PRIVATE_DETAIL'); END")
            response = await client.delete(f'/api/sessions/{sid}')
            assert response.status_code == 409 and response.json()['error']['code'] == 'session_delete_unconfirmed'
            assert (await client.get(f'/api/sessions/{sid}')).json()['messages'][-1]['content'] == 'kept'
            assert store.database.connection.execute('SELECT count(*) FROM checkpoints').fetchone()[0] > 0
            assert 'PRIVATE_DETAIL' not in response.text + caplog.text
            assert 'session_delete_failed' in caplog.text
    asyncio.run(scenario())
