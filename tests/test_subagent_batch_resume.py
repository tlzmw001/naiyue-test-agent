"""Approval scope, sequential ordering and per-thread counters survive resume."""

import json
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from native_application_helpers import async_test
from naiyue_test_agent.tools.permissions import OneUseApprovalGrants
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition
from test_subagent_execution import ChildModel, delegated_app, record_instances, role
from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run


@async_test
async def test_same_role_same_inner_call_id_approve_b_reject_a_without_cross_consumption(monkeypatch):
    effects, consumed = [], []
    child = ChildModel(replies=[call('probe', {'value': 'A'}, 'same-inner'),
        call('probe', {'value': 'B'}, 'same-inner'), AIMessage(content='B done'), AIMessage(content='A rejected')])
    root = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': 'A'}, 'id': 'outer-A'},
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': 'B'}, 'id': 'outer-B'},
    ]), AIMessage(content='done')])
    app, _ = await delegated_app(monkeypatch, child, root=root, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    instances = record_instances(monkeypatch, app)
    consume = OneUseApprovalGrants.consume
    def record(self, call_id, binding):
        outcome = consume(self, call_id, binding)
        consumed.append((call_id, binding['agent_path'], outcome))
        return outcome
    monkeypatch.setattr(OneUseApprovalGrants, 'consume', record)
    sid = await app.host.sessions.create('A')
    task = asyncio.create_task(app.host.runs.start(sid, [('user', 'two')]))
    try:
        await next_approval(app, sid, queue)
        await next_approval(app, sid, queue)
        batches = {next(instance['task'] for instance in instances if instance['agent_path'] == batch['agent_path']): batch
                   for batch in app.host.runs.pending_subagents(sid)}
        a, b = batches['A'], batches['B']
        assert a['items'][0]['tool_call_id'] == b['items'][0]['tool_call_id'] == 'same-inner'
        assert a['approval_batch_id'] != b['approval_batch_id']
        assert a['items'][0]['approval_id'] != b['items'][0]['approval_id']
        driver_b = next(instance['driver'] for instance in instances if instance['task'] == 'B')
        await submit(app, sid, b)
        await asyncio.wait_for(asyncio.shield(driver_b), 5)
        assert effects == [('probe', 'B')] and app.host.runs.pending_subagents(sid) == [a]
        assert consumed == [('same-inner', b['agent_path'], True)] and len(root.seen) == 1
        await submit(app, sid, a, ['reject'])
        result = await asyncio.wait_for(task, 5)
        assert [(m.tool_call_id, json.loads(m.content)['report']) for m in result['messages'] if isinstance(m, ToolMessage)] == [
            ('outer-A', 'A rejected'), ('outer-B', 'B done')]
        assert len(instances) == 2 and consumed == [('same-inner', b['agent_path'], True)]
    finally:
        await cancel_run(task)


@pytest.mark.parametrize('limit, status', [(2, 'error'), (3, 'success')])
@async_test
async def test_new_approval_batch_each_turn_keeps_thread_call_limit_and_original_seed(monkeypatch, limit, status):
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'first'}, 'inner-one'),
        call('probe', {'value': 'second'}, 'inner-two'), AIMessage(content='report')])
    app, root = await delegated_app(monkeypatch, child, spec=role(max_model_calls=limit),
                                  tools=[definition(calls=effects, approved=True)])
    instances = record_instances(monkeypatch, app)
    queue = approval_notifications(monkeypatch, app)
    sid = await app.host.sessions.create('A')
    task = asyncio.create_task(app.host.runs.start(sid, [('user', 'start')]))
    try:
        first = await next_approval(app, sid, queue)
        await submit(app, sid, first)
        second = await next_approval(app, sid, queue)
        assert first['approval_batch_id'] != second['approval_batch_id']
        with pytest.raises(ValueError):
            await submit(app, sid, first)
        await submit(app, sid, second)
        result = await asyncio.wait_for(task, 5)
        feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
        assert feedback.status == status and len(child.seen) == limit and len(root.seen) == 2
        assert effects == [('probe', 'first'), ('probe', 'second')] and len(instances) == 1
        snapshot = await app.host.sessions._sessions._subagents['tester']['agent'].aget_state(
            {'configurable': {'thread_id': instances[0]['child_thread_id']}})
        assert snapshot.values['thread_model_call_count'] == limit
        assert sum(isinstance(m, HumanMessage) for m in snapshot.values['messages']) == 1
    finally:
        await cancel_run(task)


@async_test
async def test_prefix_child_wait_tail_stays_ordered_and_parent_call_starts_once(monkeypatch):
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'child action'}, 'inner'), AIMessage(content='report')])
    root = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'ordered', 'args': {'value': 'prefix'}, 'id': 'prefix'},
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': 'middle'}, 'id': 'delegate'},
        {'name': 'ordered', 'args': {'value': 'tail'}, 'id': 'tail'},
    ]), AIMessage(content='done')])
    tools = [definition(calls=effects, approved=True), definition('ordered', calls=effects, sequential=True)]
    app, _ = await delegated_app(monkeypatch, child, root=root, tools=tools)
    queue = approval_notifications(monkeypatch, app)
    instances = record_instances(monkeypatch, app)
    sid = await app.host.sessions.create('A')
    await app.host.sessions.select_tools(sid, ['ordered', 'subagent'])
    task = asyncio.create_task(app.host.runs.start(sid, [('user', 'ordered task')]))
    try:
        batch = await next_approval(app, sid, queue)
        assert effects == [('ordered', 'prefix')] and len(root.seen) == 1
        await submit(app, sid, batch)
        result = await asyncio.wait_for(task, 5)
        assert effects == [('ordered', 'prefix'), ('probe', 'child action'), ('ordered', 'tail')]
        assert [m.tool_call_id for m in result['messages'] if isinstance(m, ToolMessage)] == ['prefix', 'delegate', 'tail']
        assert len(instances) == 1
    finally:
        await cancel_run(task)


@async_test
async def test_remember_approved_child_target_applies_to_parent_only_in_same_session(monkeypatch):
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'same'}, 'inner'), AIMessage(content='report')])
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'run'}, 'delegate'),
        call('probe', {'value': 'same'}, 'root-direct'), AIMessage(content='done'),
        call('probe', {'value': 'same'}, 'other'), AIMessage(content='other done')])
    app, _ = await delegated_app(monkeypatch, child, root=root, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    sid, other = await app.host.sessions.create('A'), await app.host.sessions.create('A')
    await app.host.sessions.select_tools(sid, ['subagent', 'probe'])
    await app.host.sessions.select_tools(other, ['probe'])
    task = asyncio.create_task(app.host.runs.start(sid, [('user', 'start')]))
    try:
        batch = await next_approval(app, sid, queue)
        await submit(app, sid, batch, remember=[batch['items'][0]['approval_id']])
        result = await asyncio.wait_for(task, 5)
        assert result['messages'][-1].content == 'done' and effects == [('probe', 'same'), ('probe', 'same')]
        assert len(app.host.sessions._sessions.resolve_permission_context(sid)['session_allow']) == 1
        assert app.host.sessions._sessions.resolve_permission_context(other)['session_allow'] == []
        result = await app.host.runs.start(other, [('user', 'same target')])
        assert result['__interrupt__'] and effects == [('probe', 'same'), ('probe', 'same')]
        await app.host.runs.resume_approval(other, ['reject'])
        assert effects == [('probe', 'same'), ('probe', 'same')]
        assert app.host.sessions._sessions._permission_settings.allow == ()
    finally:
        await cancel_run(task)


@async_test
async def test_root_start_approval_consumed_once_child_approval_does_not_reenter_parent(monkeypatch):
    from dataclasses import replace
    from native_application_helpers import build_application
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.tools import create_subagent_tool
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools.permissions import ToolPermission
    effects, consumed = [], []
    child = ChildModel(replies=[call('probe', {'value': 'once'}, 'inner'), AIMessage(content='report')])
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'run'}, 'delegate'),
                                       AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    app = await build_application('unused', tools=[replace(create_subagent_tool(), permissions=ToolPermission()),
        definition(calls=effects, approved=True)], initial_active_tools=['subagent'],
        system_prompt='', response_format=None, settings=AppSettings(subagents=(role(),)))
    queue = approval_notifications(monkeypatch, app)
    instances = record_instances(monkeypatch, app)
    consume = OneUseApprovalGrants.consume
    def record(self, call_id, binding):
        success = consume(self, call_id, binding)
        consumed.append((binding['agent_path'], call_id, success))
        return success
    monkeypatch.setattr(OneUseApprovalGrants, 'consume', record)
    sid = await app.host.sessions.create('A')
    result = await app.host.runs.start(sid, [('user', 'start')])
    assert result['__interrupt__'] and instances == [] and consumed == []
    task = asyncio.create_task(app.host.runs.resume_approval(sid, ['approve']))
    try:
        batch = await next_approval(app, sid, queue)
        assert consumed == [('root', 'delegate', True)] and effects == [] and len(instances) == 1
        await submit(app, sid, batch)
        result = await asyncio.wait_for(task, 5)
        assert consumed == [('root', 'delegate', True), (batch['agent_path'], 'inner', True)]
        assert len(instances) == 1 and len(root.seen) == 2 and effects == [('probe', 'once')]
        assert result['messages'][-1].content == 'done'
    finally:
        await cancel_run(task)


@async_test
async def test_two_asks_require_complete_batch_and_apply_reversed_rows_by_saved_order(monkeypatch):
    effects = []
    child = ChildModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {'value': 'first'}, 'id': 'first'},
        {'name': 'probe', 'args': {'value': 'second'}, 'id': 'second'},
    ]), AIMessage(content='partial approval report')])
    app, _ = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    queue = approval_notifications(monkeypatch, app)
    sid = await app.host.sessions.create('A')
    task = asyncio.create_task(app.host.runs.start(sid, [('user', 'two actions')]))
    try:
        batch = await next_approval(app, sid, queue)
        first, second = batch['items']
        assert (first['tool_call_id'], second['tool_call_id']) == ('first', 'second')
        decisions = [{'approval_id': second['approval_id'], 'decision': 'approve'},
                     {'approval_id': first['approval_id'], 'decision': 'reject'}]
        with pytest.raises(ValueError):
            await app.host.runs.submit_subagent_approval(sid, approval_batch_id=batch['approval_batch_id'],
                origin_run_id=batch['origin_run_id'], decisions=decisions[:1])
        assert app.host.runs.pending_subagents(sid) == [batch] and effects == []
        await app.host.runs.submit_subagent_approval(sid, approval_batch_id=batch['approval_batch_id'],
            origin_run_id=batch['origin_run_id'], decisions=decisions, remember_approved=[second['approval_id']])
        await asyncio.wait_for(task, 5)
        assert effects == [('probe', 'second')]
        messages = {m.tool_call_id: m for m in child.seen[-1] if isinstance(m, ToolMessage)}
        assert messages['first'].status == 'error' and messages['second'].status == 'success'
        assert app.host.sessions._sessions.resolve_permission_context(sid)['session_allow'][0]['args'] == {'value': 'second'}
    finally:
        await cancel_run(task)
