"""Paused roots and live children use the same root admission contract."""

import asyncio
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent.application.errors import RunCapacityExceededError, SessionPendingApprovalError
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from test_async_application import call
from test_cross_session_concurrency import concurrent_app, drain, gated
from test_dynamic_tools import definition
from test_subagent_execution import ChildModel, role, record_instances
from test_subagent_approval_resume import approval_notifications, next_approval, submit


@pytest.mark.parametrize('stop', [False, True])
@async_test
async def test_paused_root_reapplies_capacity_without_consuming_approval(monkeypatch, tmp_path, stop):
    effects, events = [], []
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite', settings=AppSettings(max_active_runs=1),
                              tools=[definition(calls=effects, approved=True)]) as (app, model):
        a, b = [await app.sessions.create_workspace('A', tmp_path) for _ in range(2)]
        model.responses[a] = [call('probe', {'value': 'approved-value'}, 'one'), AIMessage(content='after approval')]
        app.observability.subscribe(events.append)
        result = await app.runs.start(a, [('user', a)])
        assert result['approval_requests'] and effects == []
        owner = app.sessions._sessions._sessions[a]
        pending = deepcopy(owner.lifecycle.snapshot_for_run()['pending'])
        assert not owner.is_busy() and app.sessions._persistence._active == {}
        entered, _ = gated(model, b)
        task = asyncio.create_task(app.runs.start(b, [('user', b)]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            saved_events, before = len(events), app.sessions._persistence.storage.sessions.get(a)
            with pytest.raises(SessionPendingApprovalError):
                await app.runs.start(a, [('user', 'must decide first')])
            with pytest.raises(ValueError):
                await app.runs.resume_approval(a, ['invalid'])
            with pytest.raises(ValueError):
                await app.runs.resume_approval(a, ['reject'], remember_approved=['one'])
            with pytest.raises(RunCapacityExceededError):
                await app.runs.resume_approval(a, ['approve'], remember_approved=['one'])
            assert owner.lifecycle.snapshot_for_run()['pending'] == pending
            assert owner.resolve_permission_context()['session_allow'] == []
            assert not owner.is_busy() and len(events) == saved_events and app.sessions._persistence.storage.sessions.get(a) == before
            assert effects == [] and len(model.requests[a]) == 1
            if stop:
                batch, = app.runs.pending_approvals(a)
                result = await app.runs.stop_approval(a, approval_batch_id=batch['approval_batch_id'],
                                                       origin_run_id=batch['origin_run_id'])
                assert result['run_stopped'] and effects == [] and len(model.requests[a]) == 1
                assert not app.runs.pending_approvals(a) and app.sessions._persistence.storage.sessions.get(a)['state'] == 'ready'
                assert set(app.sessions._persistence._active) == {b} and not task.done()
            else:
                model.gates[b][1].set()
                await task
                result = await app.runs.resume_approval(a, ['approve'], remember_approved=['one'])
                assert result['messages'][-1].content == 'after approval'
                assert effects == [('probe', 'approved-value')] and len(model.requests[a]) == 2
                assert len(owner.resolve_permission_context()['session_allow']) == 1
                assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).tool_call_id == 'one'
                assert app.sessions._persistence.storage.sessions.get(a)['state'] == 'ready'
            assert not owner.is_busy()
        finally:
            await drain([task], model)


@pytest.mark.parametrize('close', [False, True])
@async_test
async def test_child_approval_keeps_parent_slot_and_needs_no_extra_slot(monkeypatch, tmp_path, close):
    effects, events = [], []
    child = ChildModel(replies=[call('probe', {'value': 'child'}, 'inner'), AIMessage(content='report')])
    settings = AppSettings(max_active_runs=2, subagents=(role(),))
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite', settings=settings, child=child,
                              tools=[create_subagent_tool(), definition(calls=effects, approved=True)]) as (app, model):
        a, b, c = [await app.sessions.create_workspace('A', tmp_path) for _ in range(3)]
        model.responses[a] = [call('subagent', {'agent': 'tester', 'task': 'check'}, 'delegate'), AIMessage(content='root done')]
        queue = approval_notifications(monkeypatch, app)
        instances = record_instances(monkeypatch, app)
        app.observability.subscribe(events.append)
        b_entered, _ = gated(model, b)
        tasks = [asyncio.create_task(app.runs.start(b, [('user', b)]))]
        try:
            await asyncio.wait_for(b_entered.wait(), 5)
            tasks.append(asyncio.create_task(app.runs.start(a, [('user', a)])))
            batch = await next_approval(app, a, queue)
            instance, = instances
            assert set(app.sessions._persistence._active) == {a, b} and effects == []
            assert batch['origin_run_id'] == app.sessions._persistence._active[a]
            with pytest.raises(RunCapacityExceededError):
                await app.runs.start(c, [('user', c)])
            if close:
                await asyncio.wait_for(app.aclose(), 5)
                assert all(r['run_stopped'] for r in await asyncio.gather(*tasks))
                assert instance['settled'] and instance['status'] == 'stopped' and effects == []
                assert app.sessions._persistence._active == {} and app._lifetime._operations == {}
            else:
                await submit(app, a, batch)
                result = await tasks[1]
                assert result['messages'][-1].content == 'root done'
                assert effects == [('probe', 'child')] and len(child.seen) == 2 and len(model.requests[a]) == 2
                assert set(app.sessions._persistence._active) == {b} and not tasks[0].done()
                await app.runs.start(c, [('user', c)])
                assert set(app.sessions._persistence._active) == {b}
            owner = app.sessions._sessions._sessions[a]
            assert not owner.is_busy() and not owner.approvals.pending_approval_batches()
        finally:
            await drain(tasks, model)
        assert {e.session_id for e in events if e.source} == {a}
        assert not any(e.session_id == b and e.source for e in events)
