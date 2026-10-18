"""Accepted stop preserves completed IO and checkpoint finalization facts."""

import asyncio
from threading import Event

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from naiyue_test_agent.application import SessionBusyError
from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
from native_application_helpers import async_test
from test_async_application import StreamingToolModel, call
from test_subagent_execution import ChildModel, delegated_app, record_instances
from test_subagent_approval_resume import approval_notifications, next_approval, cancel_run
from test_dynamic_tools import definition


@async_test
async def test_stop_after_io_started_retains_actual_success_and_settles_before_root_finishes(monkeypatch):
    entered, release = Event(), Event()
    effects = []
    def probe(value: str):
        """An admitted operation that must settle before its owner returns."""
        entered.set()
        assert release.wait(5)
        effects.append(value)
        return 'actual success'
    tool = StructuredTool.from_function(probe)
    spec = ToolSpec(tool, tool.args_schema, permissions=ToolPermission(default='allow'))
    child = ChildModel(replies=[call('probe', {'value': 'committed'}, 'inner')])
    app, _ = await delegated_app(monkeypatch, child, tools=[spec])
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'run')]))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        instance, = instances
        with pytest.raises(SessionBusyError): app.request_stop(sid, run_id='another', agent_path=instance['agent_path'])
        app.request_stop(sid, agent_path=instance['agent_path'])
        assert not task.done() and effects == []
        release.set()
        result = await asyncio.wait_for(task, 5)
        graph = app._sessions._subagents['tester']['agent']
        saved = await graph.aget_state({'configurable': {'thread_id': instance['child_thread_id']}})
        message = next(m for m in saved.values['messages'] if isinstance(m, ToolMessage))
        assert message.status == 'success' and message.content == 'actual success' and effects == ['committed']
        assert not saved.next and not saved.interrupts and instance['settled'] and instance['status'] == 'stopped'
        assert result['messages'][-1].content == 'root done' and len(child.seen) == 1
    finally:
        release.set()
        await cancel_run(task)


@async_test
async def test_stop_during_final_checkpoint_confirmation_does_not_relabel_a_completed_child(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    child = ChildModel(replies=[AIMessage(content='completed report')])
    app, _ = await delegated_app(monkeypatch, child)
    instances = record_instances(monkeypatch, app)
    graph = app._sessions._subagents['tester']['agent']
    read = graph.aget_state
    async def wait_final(config, *args, **kwargs):
        snapshot = await read(config, *args, **kwargs)
        if not snapshot.next and any(m.type == 'ai' and m.content == 'completed report'
                                    for m in snapshot.values.get('messages', [])):
            entered.set()
            await release.wait()
        return snapshot
    monkeypatch.setattr(graph, 'aget_state', wait_final)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'run')]))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        app.request_stop(sid, agent_path=instances[0]['agent_path'])
        release.set()
        result = await asyncio.wait_for(task, 5)
        assert instances[0]['status'] == 'completed' and instances[0]['settled']
        message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
        assert message.status == 'success' and message.content == 'completed report'
    finally:
        release.set()
        await cancel_run(task)


@async_test
async def test_finalize_rejects_late_stop_instead_of_changing_completed_report(monkeypatch):
    from naiyue_test_agent.application import session_subagents
    app, _ = await delegated_app(monkeypatch, ChildModel(replies=[AIMessage(content='report')]))
    finish = session_subagents._finish_subagent_result
    checked = []
    def check(result, instance, settings):
        with pytest.raises(SessionBusyError): app.request_stop(instance['session_id'], agent_path=instance['agent_path'])
        assert instance['phase'] == 'finalize' and not instance['control'].requested
        checked.append(instance['agent_path'])
        return finish(result, instance, settings)
    monkeypatch.setattr(session_subagents, '_finish_subagent_result', check)
    result = await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert len(checked) == 1 and result['messages'][-1].content == 'root done'


@async_test
async def test_root_stop_invalidates_two_pending_children_before_any_tool_starts(monkeypatch):
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'A'}, 'same'), call('probe', {'value': 'B'}, 'same')])
    root = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': task}, 'id': task} for task in ('A', 'B')])])
    app, _ = await delegated_app(monkeypatch, child, root=root, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'run')]))
    try:
        await next_approval(app, sid, queue)
        await asyncio.wait_for(queue.get(), 5)
        pending = [i['approval'] for i in instances]
        assert len(pending) == 2
        app.request_stop(sid)
        result = await asyncio.wait_for(task, 5)
        assert result['run_stopped'] and effects == [] and len(root.seen) == 1
        assert all(p.status == 'invalidated' and p.future.done() for p in pending)
        assert all(i['status'] == 'stopped' and i['settled'] for i in instances)
        assert app.pending_approval_batches(sid) == [] and all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values())
    finally:
        await cancel_run(task)


@async_test
async def test_root_mechanism_failure_settles_sibling_approval_without_claiming_user_stop(monkeypatch):
    release = asyncio.Event()
    effects, events = [], []
    child = ChildModel(replies=[call('probe', {'value': 'B'}, 'inner'),
        AIMessage(content='', response_metadata={'finish_reason': 'tool_calls'})])
    async def hold_a(messages):
        if any(m.type == 'human' and m.content == 'A' for m in messages): await release.wait()
    child.on_request = hold_a
    root = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': task}, 'id': task} for task in ('A', 'B')])])
    app, _ = await delegated_app(monkeypatch, child, root=root, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    instances = record_instances(monkeypatch, app)
    app.subscribe_events(events.append)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'run')]))
    try:
        await next_approval(app, sid, queue)
        b = next(i for i in instances if i['task'] == 'B')
        pending = b['approval']
        release.set()
        with pytest.raises(ModelResponseRejected): await asyncio.wait_for(task, 5)
        assert effects == [] and b['control'].reason == 'failure' and b['status'] == 'stopped'
        assert pending.status == 'invalidated' and pending.future.done()
        assert all(i['settled'] for i in instances) and all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values())
        assert not any(e.event_type == 'control_accepted' and e.data.kind == 'stop' for e in events)
        assert not app._sessions.require(sid).describe_session()['busy']
    finally:
        release.set()
        await cancel_run(task)
