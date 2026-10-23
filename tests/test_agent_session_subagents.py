"""Child instances, approvals and stopping belong to one AgentSession."""

from dataclasses import replace
import json
import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent.application.sessions.agent_session import AgentSession
from naiyue_test_agent.application.errors import SessionBusyError
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permissions import PermissionMechanismError
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition
from test_subagent_approval_resume import submit, cancel_run
from test_subagent_execution import ChildModel, delegated_app, role


@async_test
async def test_two_sessions_own_their_child_approval_and_stop_independently(monkeypatch):
    queue = asyncio.Queue()
    original = AgentSession.register_live_approval
    def notify(self, *args, **kwargs):
        pending = original(self, *args, **kwargs)
        queue.put_nowait((self.session_id, pending.approval_batch_id))
        return pending
    monkeypatch.setattr(AgentSession, 'register_live_approval', notify)
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'A'}, 'inner-a'),
        call('probe', {'value': 'B'}, 'inner-b'), AIMessage(content='child B done')])
    root = StreamingToolModel(replies=[
        call('subagent', {'agent': 'tester', 'task': 'A'}, 'outer-a'),
        call('subagent', {'agent': 'tester', 'task': 'B'}, 'outer-b'), AIMessage(content='root B done')])
    app, _ = await delegated_app(monkeypatch, child, root=root,
        tools=[definition(calls=effects, approved=True)])
    a_id, b_id = await app.sessions.create('A'), await app.sessions.create('A')
    a, b = app.sessions._sessions._sessions[a_id], app.sessions._sessions._sessions[b_id]
    first = asyncio.create_task(app.runs.start(a_id, [('user', 'check A')]))
    second = None
    try:
        assert (await asyncio.wait_for(queue.get(), 5))[0] == a_id
        a_batch, = app.runs.pending_subagents(a_id)
        second = asyncio.create_task(app.runs.start(b_id, [('user', 'check B')]))
        assert (await asyncio.wait_for(queue.get(), 5))[0] == b_id
        b_batch, = app.runs.pending_subagents(b_id)
        assert not hasattr(app.sessions._sessions, '_subagent_instances')
        assert tuple(a._run.subagent_instances) == (a_batch['agent_path'],)
        assert tuple(b._run.subagent_instances) == (b_batch['agent_path'],)
        a_instance, = a._run.subagent_instances.values()
        b_instance, = b._run.subagent_instances.values()
        assert a_instance['session_id'] == a_id and b_instance['session_id'] == b_id
        assert a_instance['agent_path'] != b_instance['agent_path']
        with pytest.raises(PermissionMechanismError, match='session'):
            b.release_live_approval(a_instance)
        assert not a_instance['approval'].future.done()
        with pytest.raises(ValueError):
            await submit(app, b_id, a_batch)
        assert a.is_busy() and b.is_busy() and effects == []
        root_run = b.snapshot_for_run()['run_id']
        app.runs.stop(a_id, run_id=a.snapshot_for_run()['run_id'])
        assert (await asyncio.wait_for(first, 5))['run_stopped']
        assert not b_instance['control'].requested and not second.done()
        assert app.runs.pending_subagents(b_id) == [b_batch]
        assert (a._run.subagent_instances if a._run else {}) == {} and a_instance['settled']
        with pytest.raises(SessionBusyError):
            b.request_scope_stop(run_id='stale-run')
        assert not b_instance['control'].requested
        await submit(app, b_id, b_batch)
        result = await asyncio.wait_for(second, 5)
        assert result['messages'][-1].content == 'root B done' and effects == [('probe', 'B')]
        assert b_instance['root_run_id'] == root_run and b_instance['settled']
        assert (b._run.subagent_instances if b._run else {}) == {} and not b.is_busy()
    finally:
        await cancel_run(first)
        if second is not None:
            await cancel_run(second)
        await app.aclose()


@async_test
async def test_role_registered_after_session_creation_uses_shared_graph(monkeypatch):
    child = ChildModel(replies=[AIMessage(content='child report')])
    app, _ = await delegated_app(monkeypatch, child, settings=AppSettings(subagents=()))
    sid = await app.sessions.create('A')
    owner = app.sessions._sessions._sessions[sid]
    assert (owner._run.subagent_instances if owner._run else {}) == {}
    await app.subagents.register(role())
    result = await app.runs.start(sid, [('user', 'check')])
    feedback = next(message for message in result['messages'] if isinstance(message, ToolMessage))
    assert json.loads(feedback.content)['report'] == 'child report' and feedback.status == 'success'
    assert app.sessions._sessions._subagents['tester']['agent'].checkpointer.backend is app.runs._agent.checkpointer
    assert (owner._run.subagent_instances if owner._run else {}) == {} and len(child.seen) == 1
    await app.aclose()


@async_test
async def test_preparation_rejects_context_for_another_session(monkeypatch):
    child = ChildModel(replies=[])
    app, _ = await delegated_app(monkeypatch, child)
    sid, other = await app.sessions.create('A'), await app.sessions.create('A')
    original = AgentSession.prepare_subagent_instance
    def forge(self, *args, **kwargs):
        kwargs['parent_context'] = replace(kwargs['parent_context'], scope=replace(kwargs['parent_context'].scope, identity=replace(kwargs['parent_context'].scope.identity, session_id=other), background_notices=None))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(AgentSession, 'prepare_subagent_instance', forge)
    with pytest.raises(PermissionMechanismError, match='session'):
        await app.runs.start(sid, [('user', 'check')])
    assert child.seen == []
    assert all((owner._run.subagent_instances if owner._run else {}) == {} for owner in app.sessions._sessions._sessions.values())
    assert not app.sessions._sessions._sessions[other].is_busy()
    await app.aclose()


@async_test
async def test_root_finish_calls_its_own_child_settle_and_check(monkeypatch):
    calls = []
    settle, check = AgentSession.settle_subagent_drivers, AgentSession.assert_subagents_settled
    async def settled(self, run_id):
        calls.append(('settle', self.session_id, run_id))
        return await settle(self, run_id)
    def checked(self, run_id):
        calls.append(('check', self.session_id, run_id))
        return check(self, run_id)
    monkeypatch.setattr(AgentSession, 'settle_subagent_drivers', settled)
    monkeypatch.setattr(AgentSession, 'assert_subagents_settled', checked)
    child = ChildModel(replies=[AIMessage(content='report')])
    app, _ = await delegated_app(monkeypatch, child)
    sid = await app.sessions.create('A')
    await app.runs.start(sid, [('user', 'check')])
    assert [item[:2] for item in calls] == [('settle', sid), ('check', sid)]
    assert calls[0][2] == calls[1][2]
    for name in ('prepare_subagent_instance', '_subagent_steps', 'register_live_approval',
                 'settle_subagent_drivers', 'assert_subagents_settled'):
        assert not hasattr(app.sessions._sessions, name)
    await app.aclose()
