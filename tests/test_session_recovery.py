"""Explicit recovery ends the old graph path without replaying its actions."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
from test_persistent_sessions import build
from test_async_persistent import factory
from test_async_application import StreamingToolModel
from test_session_history import database_facts, approval_snapshot


def unfinished(monkeypatch, path):
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        sid = app.create_session('A')
        row = storage.get(sid)
        messages = [HumanMessage(content='saved request', id='h'),
            AIMessage(content='', id='ai', tool_calls=[
                {'id': 'a', 'name': 'read', 'args': {'path': 'a'}},
                {'id': 'b', 'name': 'edit', 'args': {'path': 'b'}}]),
            ToolMessage(content=[{'type': 'text', 'text': 'actual A'}], id='actual-a',
                name='read', tool_call_id='a', artifact={'saved': [1]})]
        app._agent.update_state({'configurable': {'thread_id': row['thread_id']}}, {'messages': messages}, as_node='model')
        snapshot = app._agent.get_state({'configurable': {'thread_id': row['thread_id']}})
        storage.finish_run(sid, 'failed', None, '', 'failed')
        return sid, snapshot, messages


@pytest.mark.parametrize('asynchronous', [False, True])
def test_three_updates_preserve_results_then_only_new_input_calls_model(monkeypatch, tmp_path, asynchronous):
    path = tmp_path / 'state.sqlite'
    sid, original, messages = unfinished(monkeypatch, path)
    async def scenario():
        async with factory(monkeypatch, path, StreamingToolModel(replies=[AIMessage(content='new answer')])) as app:
            await verify(app, True)
    async def verify(app, native):
        graph = app._agent
        events, steps = [], []
        app.subscribe_events(events.append)
        if native:
            raw_before = await graph.checkpointer.aget_tuple(original.config)
            update = graph.aupdate_state
            async def recorded(config, values, *, as_node):
                steps.append(as_node)
                return await update(config, values, as_node=as_node)
            monkeypatch.setattr(graph, 'aupdate_state', recorded)
            result = await app.arecover_session(sid, checkpoint_id=original.config['configurable']['checkpoint_id'], action='continue')
            snapshot = await graph.aget_state({'configurable': {'thread_id': original.config['configurable']['thread_id']}})
            raw_after = await graph.checkpointer.aget_tuple(original.config)
        else:
            raw_before = graph.checkpointer.get_tuple(original.config)
            update = graph.update_state
            def recorded(config, values, *, as_node):
                steps.append(as_node)
                return update(config, values, as_node=as_node)
            monkeypatch.setattr(graph, 'update_state', recorded)
            result = app.recover_session(sid, checkpoint_id=original.config['configurable']['checkpoint_id'], action='continue')
            snapshot = graph.get_state({'configurable': {'thread_id': original.config['configurable']['thread_id']}})
            raw_after = graph.checkpointer.get_tuple(original.config)
        assert steps == ['__end__', 'tools', '__end__'] and events == []
        assert result['session_id'] == sid and result['state'] == 'ready'
        assert snapshot.next == () and snapshot.tasks == ()
        assert snapshot.values['messages'][:3] == messages
        recovered = snapshot.values['messages'][-1]
        assert recovered.tool_call_id == 'b' and recovered.status == 'error'
        assert 'may already have taken effect' in recovered.content
        assert recovered.additional_kwargs['naiyue_recovery']['reason'] == 'result_unconfirmed'
        assert snapshot.values['tool_execution'] == {} and snapshot.values['run_stopped'] is False
        assert raw_before.checkpoint == raw_after.checkpoint and raw_before.pending_writes == raw_after.pending_writes
        marker = json.loads(snapshot.metadata['naiyue_recovery'])
        assert marker['phase'] == 'finished' and marker['source_checkpoint_id'] == original.config['configurable']['checkpoint_id']
        row = app._sessions._persistence.storage.get(sid)
        assert row['state'] == 'ready' and row['last_run_status'] == 'recovered'
        assert row['checkpoint_id'] == result['checkpoint_id'] == snapshot.config['configurable']['checkpoint_id']
        memory = app._sessions._sessions[sid]
        assert memory['run_id'] is None and memory['session_allow'] == [] and memory['pending_tool_approval'] is None
        assert not memory['busy'] and memory['pending_turn'] is None
        answer = await app.arun(sid, [('user', 'new input')]) if native else app.run(sid, [('user', 'new input')])
        assert answer['messages'][-1].content == ('new answer' if native else 'answer')
        assert answer['messages'][:4] == snapshot.values['messages']
    if asynchronous:
        asyncio.run(scenario())
    else:
        with SessionStorage(path) as storage:
            asyncio.run(verify(build(monkeypatch, storage), False))
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        assert app.restore_session(sid) == sid


def test_stale_checkpoint_and_loaded_session_never_write(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    sid, original, _ = unfinished(monkeypatch, path)
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        before = database_facts(storage)
        with pytest.raises(SessionPersistenceError, match='checkpoint_mismatch'):
            app.recover_session(sid, checkpoint_id='old', action='continue')
        assert database_facts(storage) == before and not app._sessions.has_session(sid)
        app.recover_session(sid, checkpoint_id=original.config['configurable']['checkpoint_id'], action='continue')
        before = database_facts(storage)
        with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
            app.recover_session(sid, checkpoint_id=app.get_session_history(sid)['checkpoint_id'], action='continue')
        assert database_facts(storage) == before


@pytest.mark.parametrize('phase', ['cleared', 'paired', 'finished', 'metadata'])
def test_saved_phase_can_be_retried_after_failure_without_duplicate_results(monkeypatch, tmp_path, phase):
    path = tmp_path / 'state.sqlite'
    sid, original, _ = unfinished(monkeypatch, path)
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        update = app._agent.update_state
        saved_ids = []
        def failed_after_save(config, values, *, as_node):
            result = update(config, values, as_node=as_node)
            record = json.loads(config['metadata']['naiyue_recovery'])
            saved_ids.append(record['recovery_id'])
            if record['phase'] == phase:
                raise OSError('PRIVATE_WRITE_FAILURE')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(app._agent, 'update_state', failed_after_save)
            if phase == 'metadata':
                patch.setattr(storage, 'finish_run', lambda *_a: (_ for _ in ()).throw(OSError('PRIVATE_SQL')))
            with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
                app.recover_session(sid, checkpoint_id=original.config['configurable']['checkpoint_id'], action='continue')
        assert not app._sessions.has_session(sid) and storage.get(sid)['state'] == 'failed'
        current = app.get_session_history(sid)
        assert current['recovery']['actions'] == ['continue']
        result = app.recover_session(sid, checkpoint_id=current['checkpoint_id'], action='continue')
        assert result['recovery_id'] == saved_ids[0]
        snapshot = app._agent.get_state({'configurable': {'thread_id': original.config['configurable']['thread_id']}})
        assert [m.tool_call_id for m in snapshot.values['messages'] if isinstance(m, ToolMessage)] == ['a', 'b']
        assert snapshot.next == () and snapshot.tasks == ()


def test_pure_approval_continue_records_cancelled_before_execution(monkeypatch, tmp_path):
    from naiyue_test_agent.tools import create_session_read_tool
    row, snapshot = approval_snapshot(monkeypatch, tmp_path)
    with SessionStorage(tmp_path / 'approval.sqlite') as storage:
        app = build(monkeypatch, storage, tools=[create_session_read_tool()])
        app.recover_session(row['session_id'], checkpoint_id=snapshot.config['configurable']['checkpoint_id'], action='continue')
        result = app._agent.get_state({'configurable': {'thread_id': row['thread_id']}}).values['messages'][-1]
        assert result.additional_kwargs['naiyue_recovery']['reason'] == 'cancelled_before_approval'
        assert 'not executed' in result.content


def test_invalid_pairing_is_rejected_without_writes(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    sid, original, _ = unfinished(monkeypatch, path)
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        app._agent.update_state(original.config, {'messages': [ToolMessage(content='orphan', id='orphan', tool_call_id='other')]}, as_node='tools')
        current = app.get_session_history(sid)
        before = database_facts(storage)
        assert current['recovery']['actions'] == []
        with pytest.raises(SessionPersistenceError):
            app.recover_session(sid, checkpoint_id=current['checkpoint_id'], action='continue')
        assert database_facts(storage) == before


def test_recovery_admission_blocks_start_restore_and_other_recovery(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    sid, original, _ = unfinished(monkeypatch, path)
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        persistence = app._sessions._persistence
        persistence._recovering = sid
        before = database_facts(storage)
        for call in (lambda: persistence.start('other', 'run'), lambda: app.restore_session(sid),
                     lambda: app.recover_session(sid, checkpoint_id=original.config['configurable']['checkpoint_id'], action='continue')):
            with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
                call()
        assert database_facts(storage) == before


@pytest.mark.parametrize('invalid', ['version', 'session', 'thread', 'phase', 'source', 'approval', 'extra'])
def test_invalid_recovery_record_never_mutates_checkpoint(monkeypatch, tmp_path, invalid):
    path = tmp_path / 'state.sqlite'
    sid, original, _ = unfinished(monkeypatch, path)
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        row = storage.get(sid)
        record = {'version': 1, 'recovery_id': 'a' * 32, 'session_id': sid, 'thread_id': row['thread_id'],
            'source_checkpoint_id': original.config['configurable']['checkpoint_id'], 'source_namespace': '',
            'original_state': 'failed', 'pre_tool_approval': False, 'phase': 'cleared'}
        key, value = {'version': ('version', 9), 'session': ('session_id', 'other'), 'thread': ('thread_id', 'other'),
            'phase': ('phase', 'unknown'), 'source': ('source_checkpoint_id', 'missing'),
            'approval': ('pre_tool_approval', True), 'extra': ('unexpected', True)}[invalid]
        record[key] = value
        updated = app._agent.update_state({**original.config, 'metadata': {'naiyue_recovery': json.dumps(record)}}, None, as_node='__end__')
        before = database_facts(storage)
        with pytest.raises(SessionPersistenceError):
            app.recover_session(sid, checkpoint_id=updated['configurable']['checkpoint_id'], action='continue')
        assert database_facts(storage) == before and not app._sessions.has_session(sid)


@pytest.mark.parametrize('gate', ['blocked', 'active', 'loaded'])
def test_current_process_state_cannot_be_bypassed(monkeypatch, tmp_path, gate):
    path = tmp_path / 'state.sqlite'
    sid, original, _ = unfinished(monkeypatch, path)
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        if gate == 'blocked':
            app._sessions._persistence._blocked.add(sid)
        elif gate == 'active':
            app._sessions._persistence._active = ('other-session', 'active-run')
        else:
            app._sessions._sessions[sid] = {'manual_review_required': True}
        before = database_facts(storage)
        with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
            app.recover_session(sid, checkpoint_id=original.config['configurable']['checkpoint_id'], action='continue')
        assert database_facts(storage) == before


def test_cancelled_recovery_settles_its_write_before_allowing_retry(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    sid, original, _ = unfinished(monkeypatch, path)
    async def scenario():
        async with factory(monkeypatch, path, StreamingToolModel(replies=[])) as app:
            entered, release = asyncio.Event(), asyncio.Event()
            update = app._agent.aupdate_state
            async def pause(*args, **kwargs):
                entered.set()
                await release.wait()
                return await update(*args, **kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(app._agent, 'aupdate_state', pause)
                task = asyncio.create_task(app.arecover_session(sid,
                    checkpoint_id=original.config['configurable']['checkpoint_id'], action='continue'))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    task.cancel()
                    assert not task.done()
                    release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
            assert not app.has_session(sid) and app._sessions._persistence._recovering is None
            history = await app.aget_session_history(sid)
            assert history['checkpoint_id'] != original.config['configurable']['checkpoint_id']
            await app.arecover_session(sid, checkpoint_id=history['checkpoint_id'], action='continue')
            assert app.has_session(sid)
    asyncio.run(scenario())


def test_failure_before_new_checkpoint_can_confirm_previous_finished_record(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    sid, original, _ = unfinished(monkeypatch, path)
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        first = app.recover_session(sid, checkpoint_id=original.config['configurable']['checkpoint_id'], action='continue')
        def failing(*_a, **_kw):
            raise RuntimeError('fixture')
        monkeypatch.setattr(app._agent, 'invoke', failing)
        with pytest.raises(RuntimeError):
            app.run(sid, [('user', 'later input')])
        # The fixture failed before a new graph checkpoint; the previous finished marker remains valid.
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        history = app.get_session_history(sid)
        assert history['recovery']['actions'] == ['continue']
        repeated = app.recover_session(sid, checkpoint_id=history['checkpoint_id'], action='continue')
        assert repeated['recovery_id'] == first['recovery_id']


def test_new_model_failure_uses_its_own_checkpoint_and_recovery_record(monkeypatch, tmp_path):
    from test_application_run_events import ToolModel
    class Failing(ToolModel):
        def _generate(self, *_a, **_kw):
            raise RuntimeError('fixture')
    path = tmp_path / 'state.sqlite'
    sid, original, _ = unfinished(monkeypatch, path)
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage, Failing(responses=[]))
        first = app.recover_session(sid, checkpoint_id=original.config['configurable']['checkpoint_id'], action='continue')
        with pytest.raises(RuntimeError):
            app.run(sid, [('user', 'later input')])
    with SessionStorage(path) as storage:
        app = build(monkeypatch, storage)
        history = app.get_session_history(sid)
        assert history['checkpoint_id'] != first['checkpoint_id']
        assert history['messages'][-1].content == 'later input'
        second = app.recover_session(sid, checkpoint_id=history['checkpoint_id'], action='continue')
        assert second['recovery_id'] != first['recovery_id']
