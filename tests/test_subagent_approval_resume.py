"""Live child approval resumes one owned driver without restarting the parent."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent.tools.permissions import ToolPermission
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition
from test_subagent_execution import ChildModel, delegated_app, record_instances, role


def approval_notifications(monkeypatch, app):
    queue = asyncio.Queue()
    from naiyue_test_agent.application.agent_session import AgentSession
    register = AgentSession.register_live_approval
    def notify(self, *args, **kwargs):
        pending = register(self, *args, **kwargs)
        if app._sessions._sessions.get(self.session_id) is self:
            queue.put_nowait(pending.approval_batch_id)
        return pending
    monkeypatch.setattr(AgentSession, 'register_live_approval', notify)
    return queue


async def next_approval(app, sid, queue):
    identity = await asyncio.wait_for(queue.get(), 5)
    return next(batch for batch in app.pending_subagent_approvals(sid) if batch['approval_batch_id'] == identity)


async def submit(app, sid, batch, choices=None, *, remember=()):
    choices = choices or ['approve'] * len(batch['items'])
    return await app.asubmit_subagent_approval(sid, approval_batch_id=batch['approval_batch_id'],
        origin_run_id=batch['origin_run_id'],
        decisions=[{'approval_id': row['approval_id'], 'decision': choice}
                   for row, choice in zip(batch['items'], choices, strict=True)], remember_approved=remember)


async def cancel_run(task):
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@async_test
async def test_a_approved_and_finished_while_b_still_runs_whole_child_batch_waits(monkeypatch):
    b_entered, b_release = asyncio.Event(), asyncio.Event()
    effects, prepares = [], []
    child = ChildModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'ordinary', 'args': {'value': 'read'}, 'id': 'read'},
        {'name': 'probe', 'args': {'value': 'ask'}, 'id': 'same-inner'},
    ]), AIMessage(content='A report'), AIMessage(content='B report')])
    async def hold_b(messages):
        if next(m.content for m in messages if isinstance(m, HumanMessage)) == 'B':
            b_entered.set()
            await b_release.wait()
    child.on_request = hold_b
    root = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': 'A'}, 'id': 'outer-A'},
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': 'B'}, 'id': 'outer-B'},
    ]), AIMessage(content='root done')])
    class TracedPermission(ToolPermission):
        def prepare(self, args, context):
            prepares.append((context['agent_path'], args))
            return super().prepare(args, context)
    tools = [definition(calls=effects, approved=True, permissions=TracedPermission()),
             definition('ordinary', calls=effects)]
    app, _ = await delegated_app(monkeypatch, child, root=root,
                                spec=role(tool_names=('ordinary', 'probe')), tools=tools)
    instances = record_instances(monkeypatch, app)
    queue = approval_notifications(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'two tasks')]))
    try:
        batch = await next_approval(app, sid, queue)
        await asyncio.wait_for(b_entered.wait(), 5)
        assert batch['kind'] == 'live_child' and batch['agent_path'].startswith('root/tester/')
        assert [row['tool_call_id'] for row in batch['items']] == ['same-inner']
        assert effects == [] and len(prepares) == 1 and len(root.seen) == 1
        assert app.pending_tool_approvals(sid) == [] and not task.done()
        a = next(instance for instance in instances if instance['task'] == 'A')
        driver = a['driver']
        receipt = await submit(app, sid, batch)
        assert receipt == {'accepted': True, 'run_id': batch['origin_run_id']}
        await asyncio.wait_for(asyncio.shield(driver), 5)
        assert effects == [('ordinary', 'read'), ('probe', 'ask')]
        assert len(prepares) == 1 and len(instances) == 2 and len(root.seen) == 1
        assert not task.done() and not b_release.is_set()
        b_release.set()
        result = await asyncio.wait_for(task, 5)
        feedback = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        assert [(m.tool_call_id, m.content) for m in feedback] == [('outer-A', 'A report'), ('outer-B', 'B report')]
        snapshot = await app._sessions._subagents['tester']['agent'].aget_state(
            {'configurable': {'thread_id': a['child_thread_id']}})
        assert sum(isinstance(m, HumanMessage) for m in snapshot.values['messages']) == 1
        assert not snapshot.next and not snapshot.interrupts
        assert app.pending_subagent_approvals(sid) == [] and all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values())
    finally:
        b_release.set()
        await cancel_run(task)


@async_test
async def test_reject_is_official_tool_feedback_inside_child_and_root_continues(monkeypatch):
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'denied'}, 'inner'), AIMessage(content='cannot run')])
    app, root = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'start')]))
    try:
        batch = await next_approval(app, sid, queue)
        await submit(app, sid, batch, ['reject'])
        result = await asyncio.wait_for(task, 5)
        rejected = next(m for m in child.seen[-1] if isinstance(m, ToolMessage))
        assert rejected.tool_call_id == 'inner' and rejected.status == 'error' and effects == []
        assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).content == 'cannot run'
        assert len(root.seen) == 2 and app.pending_subagent_approvals(sid) == []
    finally:
        await cancel_run(task)


@pytest.mark.parametrize('case', ['run', 'session', 'batch', 'row', 'missing', 'duplicate', 'extra', 'remember_reject'])
@async_test
async def test_invalid_submission_keeps_original_pending_and_does_not_execute(monkeypatch, case):
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'once'}, 'inner'), AIMessage(content='done')])
    app, _ = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    sid, other = await app.acreate_session('A'), await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'start')]))
    try:
        batch = await next_approval(app, sid, queue)
        decision = {'approval_id': batch['items'][0]['approval_id'], 'decision': 'approve'}
        kwargs = dict(approval_batch_id=batch['approval_batch_id'], origin_run_id=batch['origin_run_id'], decisions=[decision])
        target = sid
        if case == 'run': kwargs['origin_run_id'] = 'stale'
        if case == 'session': target = other
        if case == 'batch': kwargs['approval_batch_id'] = 'stale'
        if case == 'row': decision['approval_id'] = 'other'
        if case == 'missing': kwargs['decisions'] = []
        if case == 'duplicate': kwargs['decisions'] *= 2
        if case == 'extra': decision['args'] = {'value': 'unreviewed'}
        if case == 'remember_reject':
            decision['decision'] = 'reject'
            kwargs['remember_approved'] = [decision['approval_id']]
        with pytest.raises(ValueError):
            await app.asubmit_subagent_approval(target, **kwargs)
        assert app.pending_subagent_approvals(sid) == [batch] and effects == []
        assert app._sessions.resolve_permission_context(sid)['session_allow'] == []
        await submit(app, sid, batch)
        with pytest.raises(ValueError):
            await submit(app, sid, batch)
        await asyncio.wait_for(task, 5)
        assert effects == [('probe', 'once')]
    finally:
        await cancel_run(task)
