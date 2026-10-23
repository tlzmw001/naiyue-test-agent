"""Approval keeps the original human intent, durable child state and task ownership."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test, build_application
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_auto_permission_review import model as review_model, verdict
from test_dynamic_tools import definition
from test_subagent_execution import ChildModel, delegated_app, record_instances, role
from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run


@async_test
async def test_auto_runs_once_before_pause_with_original_human_input(monkeypatch):
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'action'}, 'inner'), AIMessage(content='done')])
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'DELEGATED_NOT_CONSENT'}, 'delegate'),
                                       AIMessage(content='root done')])
    fast, detail = review_model([verdict('ask')]), review_model([verdict('ask')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {
        'A': root, 'B': child, 'fast': fast, 'detail': detail}))
    settings = AppSettings(subagents=(role(),), permissions={'mode': 'Auto', 'auto': {
        'fast_model_id': 'fast', 'detailed_model_id': 'detail'}})
    app = await build_application('unused', tools=[create_subagent_tool(), definition(calls=effects, approved=True)],
        initial_active_tools=['subagent'], system_prompt='', response_format=None, settings=settings)
    queue = approval_notifications(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'ORIGINAL_HUMAN_REQUEST')]))
    try:
        batch = await next_approval(app, sid, queue)
        assert effects == [] and len(fast.seen) == len(detail.seen) == 1
        data = json.loads(fast.seen[0][-1].content)
        assert [item['content'] for item in data['human_messages']] == ['ORIGINAL_HUMAN_REQUEST']
        assert 'DELEGATED_NOT_CONSENT' not in fast.seen[0][-1].content
        await submit(app, sid, batch)
        await asyncio.wait_for(task, 5)
        assert effects == [('probe', 'action')] and len(fast.seen) == len(detail.seen) == 1
    finally:
        await cancel_run(task)


@async_test
async def test_sqlite_child_approval_resumes_original_thread_and_parent_saves_report(monkeypatch, tmp_path):
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'once'}, 'inner'), AIMessage(content='child report')])
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'check'}, 'delegate'),
                                       AIMessage(content='root done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    async with open_async_persistent_application('unused', database_path=tmp_path / 'state.sqlite',
            tools=[create_subagent_tool(), definition(calls=effects, approved=True)], initial_active_tools=['subagent'],
            system_prompt='', response_format=None, settings=AppSettings(subagents=(role(max_model_calls=2),))) as app:
        queue = approval_notifications(monkeypatch, app)
        instances = record_instances(monkeypatch, app)
        sid = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(sid, [('user', 'run')]))
        try:
            batch = await next_approval(app, sid, queue)
            graph = app._sessions._subagents['tester']['agent']
            config = {'configurable': {'thread_id': instances[0]['child_thread_id']}}
            saved = await graph.aget_state(config)
            # Official after_model counting runs after the earlier HITL node;
            # the pending checkpoint already contains the model response.
            assert saved.interrupts and len(child.seen) == 1 and effects == []
            assert saved.values['messages'][-1].tool_calls[0]['id'] == 'inner'
            await submit(app, sid, batch)
            result = await asyncio.wait_for(task, 5)
            saved = await graph.aget_state(config)
            assert not saved.interrupts and not saved.next and saved.values['thread_model_call_count'] == 2
            assert [m.content for m in saved.values['messages']] == ['check', '', 'once', 'child report']
            assert effects == [('probe', 'once')] and len(instances) == 1
            assert json.loads(next(m for m in result['messages'] if isinstance(m, ToolMessage)).content)['report'] == 'child report'
            assert len(await app.alist_sessions()) == 1 and (await app.alist_sessions())[0]['state'] == 'ready'
        finally:
            await cancel_run(task)


@async_test
async def test_external_cancel_invalidates_pending_and_releases_all_owned_waiters(monkeypatch):
    child = ChildModel(replies=[call('probe', {'value': 'never'}, 'inner')])
    effects = []
    app, _ = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'run')]))
    try:
        batch = await next_approval(app, sid, queue)
        instance, = app._sessions._sessions[sid]._run.subagent_instances.values()
        pending, driver = instance['approval'], instance['driver']
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert pending.status == 'invalidated' and pending.future.result() == ([], None) and driver.done()
        assert instance['control'].reason == 'failure' and instance['status'] == 'stopped'
        assert effects == [] and all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values())
        assert app.pending_subagent_approvals(sid) == [] and not app._sessions.require(sid).describe_session()['busy']
        with pytest.raises(ValueError):
            await submit(app, sid, batch)
    finally:
        await cancel_run(task)


def test_cli_bridge_submits_live_child_without_joining_root_worker(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from naiyue_test_agent.cli.cli_async import build_local_application
    from naiyue_test_agent.application.sessions.session_approval import AgentSessionApproval
    registered = Event()
    child = ChildModel(replies=[call('probe', {'value': 'once'}, 'inner'), AIMessage(content='report')])
    root = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'check'}, 'delegate'),
                              AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    register = AgentSessionApproval.register_live_approval
    def note(self, *args, **kwargs):
        pending = register(self, *args, **kwargs)
        registered.set()
        return pending
    monkeypatch.setattr(AgentSessionApproval, 'register_live_approval', note)
    cli = build_local_application('unused', tools=[create_subagent_tool(), definition(approved=True)],
        initial_active_tools=['subagent'], system_prompt='', response_format=None,
        settings=AppSettings(subagents=(role(),)))
    gate = cli._submit(asyncio.Event)
    async def hold_root_final(_messages):
        if len(root.seen) == 1:
            await gate.wait()
    root.on_request = hold_root_final
    try:
        sid = cli.create_session('A')
        with ThreadPoolExecutor(max_workers=2) as pool:
            run = pool.submit(cli.run, sid, [('user', 'run')])
            try:
                assert registered.wait(5)
                batch, = cli.pending_subagent_approvals(sid)
                decision = [{'approval_id': batch['items'][0]['approval_id'], 'decision': 'approve'}]
                delivery = pool.submit(cli.submit_subagent_approval, sid, approval_batch_id=batch['approval_batch_id'],
                    decisions=decision, origin_run_id=batch['origin_run_id'])
                assert delivery.result(timeout=5) == {'accepted': True, 'run_id': batch['origin_run_id']}
                assert not run.done()
                cli._loop.call_soon_threadsafe(gate.set)
                assert run.result(timeout=5)['messages'][-1].content == 'done'
            finally:
                cli._loop.call_soon_threadsafe(gate.set)
                cli._submit(lambda: [task.cancel() for task in tuple(cli._app._tasks)])
    finally:
        cli.close()
