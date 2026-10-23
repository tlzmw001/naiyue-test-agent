"""Session ownership through approvals, required saving and SQLite reopening."""

import json
import asyncio
from threading import Event

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.errors import (
    SessionBusyError, SessionPendingApprovalError, SessionPersistenceError,
)
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition
from test_subagent_execution import ChildModel, record_instances, role
from test_subagent_approval_resume import approval_notifications, next_approval, submit


def persistent_delegation(monkeypatch, path, root, child, effects):
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    return open_async_persistent_application('unused', database_path=path,
        tools=[create_subagent_tool(), definition(calls=effects, approved=True)],
        initial_active_tools=['probe', 'subagent'], system_prompt='ROOT_ROLE',
        response_format=None, settings=AppSettings(subagents=(role(),)))


@pytest.mark.parametrize('outcome', ['success', 'save_failure', 'primary_error', 'cancel', 'close'])
@async_test
async def test_root_then_child_approval_keeps_owner_until_required_save(
    monkeypatch, tmp_path, caplog, outcome,
):
    effects, events, runs, closed = [], [], [], []
    primary, write_failure = RuntimeError('PRIVATE_MODEL_BODY'), OSError('PRIVATE_SAVE_BODY')
    root = StreamingToolModel(replies=[call('probe', {'value': 'parent'}, 'parent-call'),
        call('subagent', {'agent': 'tester', 'task': 'check'}, 'delegate'),
        primary if outcome == 'primary_error' else AIMessage(content='root done')])
    child = ChildModel(replies=[call('probe', {'value': 'child'}, 'child-call'),
                                AIMessage(content='child report')])
    entered, release, close_entered = asyncio.Event(), Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    async with persistent_delegation(monkeypatch, tmp_path / 'state.sqlite', root, child, effects) as app:
        sid = await app.sessions.create_workspace('A', tmp_path)
        owner = app.sessions._sessions._sessions[sid]
        instances = record_instances(monkeypatch, app)
        queue = approval_notifications(monkeypatch, app)
        def observe(event):
            events.append(event)
            if event.event_type == 'run_started' and event.source is None:
                runs.append(owner._runtime.run)
        app.observability.subscribe(observe)
        pending = await app.runs.start(sid, [('user', 'check with approval')])
        first, = runs
        assert len(pending['approval_requests']) == 1 and effects == []
        assert owner._runtime.run is None and not owner.is_busy()
        assert owner.lifecycle.snapshot_for_run()['pending']['requested_run_id'] == first.run_id
        assert app.sessions._persistence.storage.sessions.get(sid)['state'] == 'interrupted'
        assert [e.data.status for e in events if e.event_type == 'run_finished'] == ['interrupted']

        finish = app.sessions._persistence.storage.runs.finish_run
        close_catalog, stop_all = app.models._catalog.aclose, app.sessions._sessions.stop_loaded_sessions
        def blocked_finish(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            if outcome in ('save_failure', 'primary_error'):
                raise write_failure
            return finish(*args, **kwargs)
        closed_states = []
        async def close():
            closed_states.append(app.sessions._persistence.storage.sessions.get(sid)['state'])
            closed.append(owner.is_busy())
            await close_catalog()
        def closing():
            stop_all()
            close_entered.set()
        monkeypatch.setattr(app.sessions._persistence.storage.runs, 'finish_run', blocked_finish)
        monkeypatch.setattr(app.models._catalog, 'aclose', close)
        monkeypatch.setattr(app.sessions._sessions, 'stop_loaded_sessions', closing)
        task = asyncio.create_task(app.runs.resume_approval(sid, ['approve']))
        closing_task = None
        try:
            batch = await next_approval(app, sid, queue)
            second = owner._runtime.run
            instance, = instances
            driver, future = instance['driver'], instance['approval'].future
            assert second is runs[1] and second is not first and second.run_id != first.run_id
            assert batch['origin_run_id'] == instance['root_run_id'] == second.run_id
            assert effects == [('probe', 'parent')] and len(root.seen) == 2 and len(child.seen) == 1
            assert owner.is_busy() and not task.done() and not future.done()
            with pytest.raises(SessionPendingApprovalError):
                app.sessions._sessions._admit(sid, 'duplicate', 'message')
            with pytest.raises(SessionBusyError):
                app.sessions._sessions._admit(sid, 'duplicate', 'approval_resume', decisions=['approve'])
            assert owner._runtime.run is second
            await submit(app, sid, batch)
            await asyncio.wait_for(entered.wait(), 5)
            assert driver.done() and future.done() and instance['settled']
            assert 'driver' not in instance and 'approval' not in instance
            assert owner._runtime.run is second and owner.is_busy() and not second.controls_open
            assert second.subagent_instances == {} and owner.approvals.pending_approval_batches() == []
            assert effects == [('probe', 'parent'), ('probe', 'child')]
            assert len(runs) == 2 and len(root.seen) == 3 and len(child.seen) == 2
            assert [(e.source is None, e.data.status) for e in events if e.event_type == 'run_finished'] == [
                (True, 'interrupted'), (False, 'interrupted'), (False, 'completed')]
            assert app.sessions._persistence.storage.sessions.get(sid)['state'] == 'running' and closed == []
            if outcome == 'cancel':
                task.cancel('cancel during required save')
                barrier = loop.create_future()
                loop.call_soon(barrier.set_result, None)
                await barrier
                assert not task.done() and owner.is_busy()
            elif outcome == 'close':
                closing_task = asyncio.create_task(app.aclose())
                await asyncio.wait_for(close_entered.wait(), 5)
                assert not closing_task.done() and owner.is_busy() and closed == []
            release.set()
            if outcome == 'primary_error':
                with pytest.raises(RuntimeError) as caught:
                    await task
                assert caught.value is primary
                assert 'session_persistence_secondary_failed' in caplog.text
            elif outcome == 'save_failure':
                with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed') as caught:
                    await task
                assert caught.value.__cause__ is write_failure
            elif outcome == 'cancel':
                with pytest.raises(asyncio.CancelledError) as caught:
                    await task
                assert caught.value.args == ('cancel during required save',)
            else:
                result = await task
                assert result['messages'][-1].content == 'root done'
                feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage) and m.name == 'subagent')
                assert json.loads(feedback.content)['report'] == 'child report' and feedback.artifact['agent_path'] == instance['agent_path']
            if closing_task:
                await closing_task
            assert owner._runtime.run is None and not owner.is_busy() and app._lifetime._operations == {}
            assert events[-1].event_type == 'run_finished' and events[-1].source is None
            failed = outcome in ('save_failure', 'primary_error')
            assert events[-1].data.status == ('failed' if failed else 'completed')
            state = closed_states[-1] if closing_task else app.sessions._persistence.storage.sessions.get(sid)['state']
            assert state == ('running' if failed else 'ready')
            if failed:
                assert not (await app.sessions.status(sid))['can_run']
            assert closed == ([False] if outcome == 'close' else [])
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, *([closing_task] if closing_task else []), return_exceptions=True)
    assert closed == [False]
    assert 'PRIVATE_MODEL_BODY' not in caplog.text and 'PRIVATE_SAVE_BODY' not in caplog.text
    assert not any(marker in caplog.text for marker in ('observation_failed', 'run_event_publish_failed'))


@async_test
async def test_root_approval_reopens_as_facts_until_explicit_decision(monkeypatch, tmp_path):
    effects, path = [], tmp_path / 'state.sqlite'
    root = StreamingToolModel(replies=[call('probe', {'value': 'once'}, 'parent-call'), AIMessage(content='done')])
    child = ChildModel(replies=[])
    async with persistent_delegation(monkeypatch, path, root, child, effects) as app:
        sid = await app.sessions.create_workspace('A', tmp_path)
        await app.runs.start(sid, [('user', 'await permission')])
        origin = app.runs.pending_approvals(sid)[0]['origin_run_id']
        assert app.sessions._sessions._sessions[sid]._runtime.run is None and effects == []
    async with persistent_delegation(monkeypatch, path, root, child, effects) as app:
        history = await app.history.session(sid)
        assert history['recovery']['kind'] == 'pending_approval'
        await app.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
        owner = app.sessions._sessions._sessions[sid]
        assert owner._runtime.run is None and not owner.is_busy() and owner.subagents.child_thread_ids() == ()
        assert owner.resolve_permission_context()['session_allow'] == []
        assert owner.lifecycle.snapshot_for_run()['pending_turn'] is None and owner.lifecycle.resolve_prompt_parts() == []
        batch, = app.runs.pending_approvals(sid)
        assert batch['kind'] == 'paused_root' and batch['origin_run_id'] == origin
        assert effects == [] and len(root.seen) == 1 and child.seen == []
        result = await app.runs.resume_approval(sid, ['approve'])
        assert result['messages'][-1].content == 'done' and effects == [('probe', 'once')]
        assert owner._runtime.run is None and app.runs.pending_approvals(sid) == []
        assert app.sessions._persistence.storage.sessions.get(sid)['state'] == 'ready' and len(root.seen) == 2


@async_test
async def test_close_during_child_approval_settles_before_reopen(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.storage import resources as storage_resources
    order = []
    connection_close = storage_resources.aiosqlite.Connection.close
    storage_close = storage_resources.ProjectStore.close
    async def close_connection(connection):
        await connection_close(connection)
        order.append('saver')
    def close_storage(storage):
        storage_close(storage)
        order.append('storage')
    monkeypatch.setattr(storage_resources.aiosqlite.Connection, 'close', close_connection)
    monkeypatch.setattr(storage_resources.ProjectStore, 'close', close_storage)
    path, effects, events, closed = tmp_path / 'state.sqlite', [], [], []
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'check'}, 'delegate')])
    child = ChildModel(replies=[call('probe', {'value': 'never'}, 'child-call')])
    async with persistent_delegation(monkeypatch, path, root, child, effects) as app:
        sid = await app.sessions.create_workspace('A', tmp_path)
        owner = app.sessions._sessions._sessions[sid]
        instances = record_instances(monkeypatch, app)
        queue = approval_notifications(monkeypatch, app)
        app.observability.subscribe(events.append)
        close_catalog = app.models._catalog.aclose
        async def close():
            closed.append((owner.is_busy(), [(i['settled'], 'driver' in i, 'approval' in i) for i in instances],
                           app.sessions._persistence.storage.sessions.get(sid)['state']))
            await close_catalog()
            order.append('catalog')
        monkeypatch.setattr(app.models._catalog, 'aclose', close)
        task = asyncio.create_task(app.runs.start(sid, [('user', 'stop awaiting child')]))
        try:
            batch = await next_approval(app, sid, queue)
            instance, = instances
            future, driver = instance['approval'].future, instance['driver']
            parent = owner._runtime.run
            assert owner.is_busy() and batch['origin_run_id'] == parent.run_id
            await asyncio.wait_for(app.aclose(), 5)
            result = await task
            assert result['run_stopped'] and effects == []
            assert future.done() and driver.done() and instance['status'] == 'stopped'
            assert owner._runtime.run is None and closed == [(False, [(True, False, False)], 'ready')]
            assert app._lifetime._operations == {} and app.sessions._persistence.storage.database._closed
            assert [(e.source is None, e.data.status) for e in events if e.event_type == 'run_finished'] == [
                (False, 'interrupted'), (False, 'stopped'), (True, 'stopped')]
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert order == ['catalog', 'saver', 'storage']
    async with persistent_delegation(monkeypatch, path, root, child, effects) as app:
        await app.sessions.restore(sid)
        owner = app.sessions._sessions._sessions[sid]
        assert owner._runtime.run is None and owner.subagents.child_thread_ids() == ()
        assert app.runs.pending_approvals(sid) == [] and effects == []
        saved_child, = await app.history.subagents(sid)
        assert saved_child['status'] == 'stopped'
        history = await app.history.subagent(sid, saved_child['agent_id'])
        assert history['status'] == 'stopped' and history['available']
        assert len(root.seen) == 1 and len(child.seen) == 1
    assert not any(marker in caplog.text for marker in ('observation_failed', 'run_event_publish_failed'))


@async_test
async def test_compaction_reopens_configuration_and_archive_without_run_resources(monkeypatch, tmp_path):
    from test_manual_compaction import compact_model
    path, effects = tmp_path / 'state.sqlite', []
    root, child = compact_model(), ChildModel(replies=[])
    async with persistent_delegation(monkeypatch, path, root, child, effects) as app:
        sid = await app.sessions.create_workspace('A', tmp_path)
        await app.runs.start(sid, [('user', 'old task')])
        await app.runs.start(sid, [('user', 'recent task')])
        original = await app.history.messages(sid)
        await app.sessions.select_permission_mode(sid, 'NoAsk')
        await app.sessions.select_tools(sid, ['probe'])
        result = await app.runs.compact(sid)
        assert result['compaction_status'] == 'applied'
        compacted = await app.history.messages(sid)
        assert compacted[0].additional_kwargs['naiyue_compaction_summary']
        assert [m.id for m in compacted[1:]] == [m.id for m in original[-2:]]
        assert app.sessions._sessions._sessions[sid]._runtime.run is None and len(root.seen) == 3
        page = await app.history.page(sid, limit=50, body_bytes=128000, preview_chars=100)
        assert page['has_compaction'] and not page['has_more']
        assert {r['message_id'] for r in page['records']} == {m.id for m in original}
        thread = app.sessions._sessions.require(sid).get_thread_id()
    async with persistent_delegation(monkeypatch, path, root, child, effects) as app:
        await app.sessions.prepare(sid)
        owner = app.sessions._sessions._sessions[sid]
        assert owner._runtime.run is None and owner.subagents.child_thread_ids() == () and not owner.is_busy()
        assert owner.get_thread_id() == thread and owner.get_permission_mode() == 'NoAsk'
        assert owner.get_active_tools() == ('probe',) and owner.lifecycle.resolve_prompt_parts() == []
        assert owner.resolve_permission_context()['session_allow'] == []
        assert [m.id for m in await app.history.messages(sid)] == [m.id for m in compacted]
        reopened = await app.history.page(sid, limit=50, body_bytes=128000, preview_chars=100)
        assert reopened['records'] == page['records'] and reopened['has_compaction']
        assert len(root.seen) == 3 and child.seen == [] and effects == []
