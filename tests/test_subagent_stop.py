"""Scope stops settle one child without changing its sibling's authority."""

import json
import asyncio
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition
from test_subagent_execution import ChildModel, delegated_app, record_instances
from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run
from threading import Event, Thread


@pytest.mark.parametrize('approve_first', [False, True])
@async_test
async def test_stop_one_pending_child_keeps_sibling_running_and_decision_history(monkeypatch, approve_first):
    entered, release = asyncio.Event(), asyncio.Event()
    effects, events = [], []
    child = ChildModel(replies=[call('probe', {'value': 'A'}, 'same-inner'), AIMessage(content='B report')])
    async def hold_b(messages):
        if any(message.type == 'human' and message.content == 'B' for message in messages):
            entered.set()
            await release.wait()
    child.on_request = hold_b
    root = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': task}, 'id': 'outer-' + task} for task in ('A', 'B')]),
        AIMessage(content='root continued')])
    app, _ = await delegated_app(monkeypatch, child, root=root, tools=[definition(calls=effects, approved=True)])
    instances = record_instances(monkeypatch, app)
    app.subscribe_events(events.append)
    queue = approval_notifications(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'two checks')]))
    try:
        batch = await next_approval(app, sid, queue)
        await asyncio.wait_for(entered.wait(), 5)
        a = next(instance for instance in instances if instance['task'] == 'A')
        b = next(instance for instance in instances if instance['task'] == 'B')
        pending = a['approval']
        driver = a['driver']
        if approve_first: await submit(app, sid, batch)
        assert app.request_stop(sid, run_id=batch['origin_run_id'], agent_path=a['agent_path']) == []
        done, _ = await asyncio.wait((driver,), timeout=5)
        assert done and a['status'] == 'stopped'
        assert not task.done() and not b['control'].requested and not app._sessions.is_stop_requested(sid)
        assert pending.status == ('accepted' if approve_first else 'invalidated')
        assert pending.decisions == (('approve',) if approve_first else None)
        with pytest.raises(ValueError): await submit(app, sid, batch)
        assert effects == [] and app.pending_approval_batches(sid) == []
        release.set()
        result = await asyncio.wait_for(task, 5)
        feedback = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
        assert feedback['outer-A'].status == 'error' and 'stopped' in feedback['outer-A'].content.lower()
        assert json.loads(feedback['outer-B'].content)['report'] == 'B report' and result['messages'][-1].content == 'root continued'
        assert [(e.data.status) for e in events if e.source and e.source.agent_path == a['agent_path']
                and e.event_type == 'run_finished'] == ['interrupted', 'stopped']
    finally:
        release.set()
        await cancel_run(task)


@async_test
async def test_root_stop_between_saved_interrupt_and_registration_cannot_leave_an_orphan(monkeypatch):
    child = ChildModel(replies=[call('probe', {'value': 'never'}, 'inner')])
    effects = []
    app, root = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    instances = record_instances(monkeypatch, app)
    from naiyue_test_agent.application.agent_session import AgentSession
    register = AgentSession.register_live_approval
    def stop_then_register(self, instance, *args):
        app.request_stop(instance['session_id'], run_id=instance['root_run_id'])
        return register(self, instance, *args)
    monkeypatch.setattr(AgentSession, 'register_live_approval', stop_then_register)
    sid = await app.acreate_session('A')
    result = await asyncio.wait_for(app.arun(sid, [('user', 'start')]), 5)
    assert result['run_stopped'] and effects == [] and len(root.seen) == 1
    assert instances[0]['status'] == 'stopped' and instances[0]['settled']
    assert app.pending_approval_batches(sid) == [] and all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values())


@async_test
async def test_close_stops_pending_child_before_waiting_and_closing_catalog(monkeypatch):
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'never'}, 'inner')])
    app, _ = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'start')]))
    try:
        await next_approval(app, sid, queue)
        await asyncio.wait_for(app.aclose(), 5)
        result = await task
        assert result['run_stopped'] and effects == [] and app._closed
        instance, = instances
        assert instance['control'].reason == 'close' and instance['settled'] and instance['status'] == 'stopped'
    finally:
        await cancel_run(task)


@async_test
async def test_cross_thread_stop_is_visible_before_approval_registration_and_notifications(monkeypatch):
    child = ChildModel(replies=[call('probe', {'value': 'never'}, 'inner')])
    effects, errors, stoppers = [], [], []
    app, root = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    instances = record_instances(monkeypatch, app)
    from naiyue_test_agent.application.agent_session import AgentSession
    register = AgentSession.register_live_approval
    notified, release = Event(), Event()
    def stop_then_register(self, instance, *args):
        request = instance['control'].request
        def delayed_notification(reason='user'):
            notified.set()
            assert release.wait(5)
            request(reason)
        monkeypatch.setattr(instance['control'], 'request', delayed_notification)
        def stop():
            try: app.request_stop(instance['session_id'], agent_path=instance['agent_path'])
            except BaseException as error: errors.append(error)
        stopper = Thread(target=stop)
        stoppers.append(stopper)
        stopper.start()
        assert notified.wait(5)
        return register(self, instance, *args)
    monkeypatch.setattr(AgentSession, 'register_live_approval', stop_then_register)
    try:
        result = await asyncio.wait_for(app.arun(await app.acreate_session('A'), [('user', 'start')]), 5)
        assert result['messages'][-1].content == 'root done' and len(root.seen) == 2
        feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
        assert feedback.status == 'error' and 'stopped' in feedback.content.lower()
        assert effects == [] and instances[0]['status'] == 'stopped' and instances[0]['settled']
    finally:
        release.set()
        for stopper in stoppers: await asyncio.to_thread(stopper.join, 5)
    assert not errors
