"""A new delegation reuses history, but owns its own controls and model cap."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from native_application_helpers import async_test
from naiyue_test_agent.tools.subagent import SubAgentInput
from test_async_application import call, StreamingToolModel
from test_subagent_execution import ChildModel, delegated_app, record_instances, role


def feedback(result):
    return [m for m in result['messages'] if isinstance(m, ToolMessage) and m.name == 'subagent'][-1]


async def again(app, sid, root, identity, *, task='second task', agent='tester'):
    root.replies.extend([call('subagent', {'agent': agent, 'task': task, 'agent_id': identity}, 'next'),
                         AIMessage(content='root done again')])
    return await app.arun(sid, [('user', task)])


def test_optional_instance_id_is_full_and_strict():
    assert SubAgentInput(agent='tester', task='work', agent_id=None).agent_id is None
    assert SubAgentInput(agent='tester', task='work', agent_id='a' * 64).agent_id == 'a' * 64
    for bad in ('short', 'A' * 64, '../outside', 1, '', 'a' * 65):
        with pytest.raises(ValidationError):
            SubAgentInput(agent='tester', task='work', agent_id=bad)


@async_test
async def test_same_instance_next_user_turn_keeps_history_new_controls_and_usage(monkeypatch):
    first = AIMessage(content='first report', usage_metadata={'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5})
    second = AIMessage(content='second report', usage_metadata={'input_tokens': 7, 'output_tokens': 4, 'total_tokens': 11})
    child = ChildModel(replies=[first, second])
    app, root = await delegated_app(monkeypatch, child, spec=role(max_model_calls=1))
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_session('A')
    result = await app.arun(sid, [('user', 'first request')])
    original = feedback(result)
    envelope = json.loads(original.content)
    identity = envelope['agent_id']
    assert envelope == dict(agent_id=identity, agent='tester', status='completed', report='first report', truncated=False)
    follow = feedback(await again(app, sid, root, identity))
    assert json.loads(follow.content)['agent_id'] == identity
    assert json.loads(follow.content)['report'] == 'second report'
    assert original.content == feedback(result).content and original.artifact['usage']['total_tokens'] == 5
    assert follow.artifact['usage']['total_tokens'] == 11
    a, b = instances
    assert a['child_thread_id'] == b['child_thread_id'] and a['agent_path'] == b['agent_path']
    assert a['seed_id'] != b['seed_id'] and a['parent_run_id'] != b['parent_run_id']
    assert a['control'] is not b['control'] and a['run_id'] != b['run_id']
    snapshot = await app._sessions._subagents['tester']['agent'].aget_state({'configurable': {'thread_id': b['child_thread_id']}})
    assert [m.content for m in snapshot.values['messages']] == ['check this task', 'first report', 'second task', 'second report']
    assert snapshot.values['thread_model_call_count'] == 1
    assert [m.content for m in child.seen[1] if isinstance(m, HumanMessage)] == ['check this task', 'second task']
    records = await app.aget_subagents(sid)
    assert len(records) == 1 and records[0]['agent_id'] == identity
    assert all('driver' not in item and item['settled'] for item in instances)


@async_test
async def test_same_parent_can_delegate_again_after_success_and_omitting_id_creates_clean_history(monkeypatch):
    child = ChildModel(replies=[AIMessage(content='first'), AIMessage(content='continued'), AIMessage(content='clean')])
    root = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'one'}, 'first'),
                              AIMessage(content='replace'), AIMessage(content='root done')])
    identity = None
    async def continue_after_report(messages):
        nonlocal identity
        if isinstance(messages[-1], ToolMessage) and identity is None:
            identity = json.loads(messages[-1].content)['agent_id']
            root.replies[0] = call('subagent', {'agent': 'tester', 'task': 'two', 'agent_id': identity}, 'second')
    root.on_request = continue_after_report
    app, _ = await delegated_app(monkeypatch, child, root=root)
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_session('A')
    await app.arun(sid, [('user', 'two steps')])
    assert len(instances) == 2 and instances[0]['agent_path'] == instances[1]['agent_path']
    root.replies.extend([call('subagent', {'agent': 'tester', 'task': 'unrelated', 'agent_id': None}, 'new'), AIMessage(content='done')])
    latest = feedback(await app.arun(sid, [('user', 'unrelated')]))
    assert json.loads(latest.content)['agent_id'] != identity
    assert [m.content for m in child.seen[-1] if isinstance(m, HumanMessage)] == ['unrelated']


@pytest.mark.parametrize('case', ['unknown', 'other_session', 'wrong_role', 'failed', 'checkpoint_changed'])
@async_test
async def test_invalid_continuation_never_starts_model(monkeypatch, case):
    child = ChildModel(replies=[TimeoutError('failed child')] if case == 'failed' else [AIMessage(content='first')])
    app, root = await delegated_app(monkeypatch, child)
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_session('A')
    result = await app.arun(sid, [('user', 'first')])
    identity = instances[0]['agent_path'].rsplit('/', 1)[-1]
    target, agent = sid, 'tester'
    if case == 'unknown': identity = 'a' * 64
    if case == 'other_session': target = await app.acreate_session('A')
    if case == 'wrong_role':
        await app.aregister_subagent(role(name='other'))
        agent = 'other'
    if case == 'checkpoint_changed':
        graph = app._sessions._subagents['tester']['agent']
        await graph.aupdate_state({'configurable': {'thread_id': instances[0]['child_thread_id']}}, {'messages': [HumanMessage(content='unconfirmed change')]})
    count = len(child.seen)
    response = feedback(await again(app, target, root, identity, agent=agent))
    assert response.status == 'error' and len(child.seen) == count
    assert result['messages'][-1].content == 'root done'
    if case == 'other_session':
        assert await app.aget_subagents(target) == []
        with pytest.raises(LookupError):
            await app.aget_subagent_history(target, identity)


@async_test
async def test_concurrent_delegations_to_same_instance_get_busy_without_queue(monkeypatch):
    child = ChildModel(replies=[AIMessage(content='first'), AIMessage(content='continued')])
    app, root = await delegated_app(monkeypatch, child)
    sid = await app.acreate_session('A')
    identity = json.loads(feedback(await app.arun(sid, [('user', 'first')])).content)['agent_id']
    root.replies.extend([AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': str(i), 'agent_id': identity}, 'id': f'parallel-{i}'}
        for i in range(2)]), AIMessage(content='done')])
    result = await app.arun(sid, [('user', 'parallel')])
    responses = [m for m in result['messages'] if isinstance(m, ToolMessage) and m.tool_call_id.startswith('parallel-')]
    assert sorted(m.status for m in responses) == ['error', 'success']
    assert 'busy' in next(m.content for m in responses if m.status == 'error').lower()
    assert len(child.seen) == 2


@async_test
async def test_approval_resume_shares_cap_but_next_delegation_resets_it(monkeypatch):
    from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run
    from test_dynamic_tools import definition
    child = ChildModel(replies=[call('probe', {'value': 'first'}, 'inner-1'), AIMessage(content='first report'),
                                call('probe', {'value': 'second'}, 'inner-2'), AIMessage(content='second report')])
    effects = []
    app, root = await delegated_app(monkeypatch, child, spec=role(max_model_calls=2), tools=[definition(calls=effects, approved=True)])
    sid = await app.acreate_session('A')
    queue = approval_notifications(monkeypatch, app)
    identity = None
    for index in range(2):
        if index:
            task = asyncio.create_task(again(app, sid, root, identity))
        else:
            task = asyncio.create_task(app.arun(sid, [('user', 'first')]))
        try:
            batch = await next_approval(app, sid, queue)
            instance = next(iter(app._sessions.require(sid)._run.subagent_instances.values()))
            graph = app._sessions._subagents['tester']['agent']
            config = {'configurable': {'thread_id': instance['child_thread_id']}}
            assert (await graph.aget_state(config)).values['thread_model_call_count'] == 1
            await submit(app, sid, batch)
            identity = json.loads(feedback(await asyncio.wait_for(task, 5)).content)['agent_id']
            assert (await graph.aget_state(config)).values['thread_model_call_count'] == 2
        finally:
            await cancel_run(task)
    assert effects == [('probe', 'first'), ('probe', 'second')]


@async_test
async def test_approval_cannot_bypass_exhausted_model_limit(monkeypatch):
    from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run
    from test_dynamic_tools import definition
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'effect'}, 'inner'), AIMessage(content='must not run')])
    app, _ = await delegated_app(monkeypatch, child, spec=role(max_model_calls=1), tools=[definition(calls=effects, approved=True)])
    sid = await app.acreate_session('A')
    queue = approval_notifications(monkeypatch, app)
    task = asyncio.create_task(app.arun(sid, [('user', 'first')]))
    try:
        await submit(app, sid, await next_approval(app, sid, queue))
        response = feedback(await asyncio.wait_for(task, 5))
        assert response.status == 'error' and 'limit' in response.content
        assert len(child.seen) == 1 and effects == [('probe', 'effect')]
    finally:
        await cancel_run(task)


@async_test
async def test_small_result_budget_rejects_before_child_model(monkeypatch):
    from naiyue_test_agent.settings import AppSettings
    child = ChildModel(replies=[])
    app, _ = await delegated_app(monkeypatch, child, settings=AppSettings(
        subagents=(role(),), tools={'subagent': {'max_result_bytes': 100}}))
    sid = await app.acreate_session('A')
    response = feedback(await app.arun(sid, [('user', 'work')]))
    assert response.status == 'error' and 'metadata' in response.content
    assert child.seen == [] and app._sessions.require(sid)._subagent_records == {}


@async_test
async def test_changed_role_rejects_without_consuming_old_ready_instance(monkeypatch):
    from dataclasses import replace
    child = ChildModel(replies=[AIMessage(content='first')])
    app, root = await delegated_app(monkeypatch, child)
    sid = await app.acreate_session('A')
    identity = json.loads(feedback(await app.arun(sid, [('user', 'first')])).content)['agent_id']
    registered = app._sessions._subagents['tester']
    registered['spec'] = replace(registered['spec'], system_prompt='Changed execution definition')
    response = feedback(await again(app, sid, root, identity))
    assert response.status == 'error' and 'configuration changed' in response.content
    assert len(child.seen) == 1 and app._sessions.require(sid)._subagent_records[identity]['state'] == 'ready'


@pytest.mark.parametrize('scope', ['root', 'child'])
@async_test
async def test_stop_after_claim_before_input_restores_previous_ready_record(monkeypatch, scope):
    from naiyue_test_agent.application import subagent_records
    child = ChildModel(replies=[AIMessage(content='first')])
    app, root = await delegated_app(monkeypatch, child)
    sid = await app.acreate_session('A')
    identity = json.loads(feedback(await app.arun(sid, [('user', 'first')])).content)['agent_id']
    previous = app._sessions.require(sid)._subagent_records[identity].copy()
    admit = subagent_records.admit
    async def stop_before_input(owner, graph, instance, spec):
        await admit(owner, graph, instance, spec)
        owner.request_scope_stop(run_id=instance['parent_run_id'], agent_path=instance['agent_path'] if scope == 'child' else 'root')
    monkeypatch.setattr(subagent_records, 'admit', stop_before_input)
    await again(app, sid, root, identity)
    saved = app._sessions.require(sid)._subagent_records[identity]
    assert saved['state'] == 'ready' and saved['checkpoint_id'] == previous['checkpoint_id']
    assert saved['last_delegation_id'] == previous['last_delegation_id'] and len(child.seen) == 1
