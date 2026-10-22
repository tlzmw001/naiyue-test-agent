"""A delegated task has its own graph history, model snapshot and tool scope."""

import json
import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import Field

from native_application_helpers import async_test, build_application
from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.run_services import current_run_services
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import SubAgentSpec, create_subagent_tool, create_session_read_tool
from naiyue_test_agent.settings import AppSettings, load_settings
from naiyue_test_agent.tools.permissions import ToolPermission, PermissionMechanismError
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition


class ChildModel(StreamingToolModel):
    services: list = Field(default_factory=list)
    bound: list = Field(default_factory=list)
    on_request: Any = None

    def bind_tools(self, tools, **kwargs):
        self.bound.append(tuple(tool.name for tool in tools))
        return self

    async def _next(self, messages):
        self.services.append(current_run_services())
        if self.on_request is not None:
            await self.on_request(messages)
        return await super()._next(messages)


def role(**kwargs):
    fields = dict(name='tester', description='Verify tests', system_prompt='CHILD_ROLE_PROMPT',
                  tool_names=('probe',), model_id='B')
    return SubAgentSpec(**{**fields, **kwargs})


async def delegated_app(monkeypatch, child, *, root=None, spec=None, tools=None, settings=None):
    root = root or StreamingToolModel(replies=[
        call('subagent', {'agent': 'tester', 'task': 'check this task'}, 'delegate'), AIMessage(content='root done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    settings = settings or AppSettings(subagents=(spec or role(),))
    app = await build_application('unused', tools=[create_subagent_tool(), *(tools or [definition()])],
                                  initial_active_tools=['subagent'], system_prompt='ROOT_PROMPT',
                                  response_format=None, settings=settings)
    return app, root


def record_instances(monkeypatch, app):
    instances = []
    from naiyue_test_agent.application.agent_session import AgentSession
    prepare = AgentSession.prepare_subagent_instance
    def record(self, *args, **kwargs):
        graph, instance = prepare(self, *args, **kwargs)
        if app._sessions._sessions.get(self.session_id) is self:
            instances.append(instance)
        return graph, instance
    monkeypatch.setattr(AgentSession, 'prepare_subagent_instance', record)
    return instances


@async_test
async def test_parent_receives_only_report_and_artifact_child_uses_real_read(monkeypatch, tmp_path):
    (tmp_path / 'tests.txt').write_text('one actual test\n', encoding='utf-8')
    child = ChildModel(replies=[call('read', {'path': 'tests.txt'}, 'inner'), AIMessage(content='verified report')])
    spec = SubAgentSpec('tester', 'Read tests', 'CHILD_ROLE_PROMPT', ('read',), model_id='B')
    app, root = await delegated_app(monkeypatch, child, spec=spec, tools=[create_session_read_tool()])
    instances = record_instances(monkeypatch, app)
    events = []
    app.subscribe_events(events.append)
    sid = await app.acreate_workspace_session('A', tmp_path)
    result = await app.arun(sid, [('user', 'ORIGINAL_HUMAN_REQUEST')])
    outer = [message for message in result['messages'] if isinstance(message, ToolMessage)]
    assert len(outer) == 1
    assert (outer[0].tool_call_id, outer[0].status, json.loads(outer[0].content)['report']) == ('delegate', 'success', 'verified report')
    artifact = outer[0].artifact
    assert artifact['model_id'] == 'B' and artifact['status'] == 'completed' and artifact['usage'] is None
    instance, = instances
    assert artifact['agent_path'] == instance['agent_path']
    assert instance['agent_path'].startswith('root/tester/') and instance['status'] == 'completed'
    child_graph = app._sessions._subagents['tester']['agent']
    assert child_graph.checkpointer.backend is app._agent.checkpointer
    snapshot = await child_graph.aget_state({'configurable': {'thread_id': instance['child_thread_id']}})
    messages = snapshot.values['messages']
    assert [message.content for message in messages] == ['check this task', '', 'one actual test\n', 'verified report']
    assert messages[0].additional_kwargs.get('naiyue_human_input') is not True
    assert messages[0].additional_kwargs['naiyue_input_source'] == 'delegated_task'
    assert child.bound == [('read',), ('read',)]
    assert 'CHILD_ROLE_PROMPT' in child.seen[0][0].content
    assert 'ROOT_PROMPT' not in child.seen[0][0].content
    assert all('ORIGINAL_HUMAN_REQUEST' not in str(message.content) for message in child.seen[0])
    assert len(root.seen) == 2 and 'one actual test' not in str(root.seen[1])
    assert all(service.publisher.source.agent_path == instance['agent_path'] and service.turns is not None
               for service in child.services)
    assert all(service.control is instance['control'] for service in child.services)
    assert current_run_services() is None
    assert sum(event.event_type == 'run_started' and event.source is None for event in events) == 1
    assert sum(event.event_type == 'run_finished' and event.source is None for event in events) == 1
    assert sum(event.event_type == 'run_started' and event.source is not None for event in events) == 1
    assert any(event.source and getattr(event.data, 'tool_name', None) == 'read' for event in events)


@pytest.mark.parametrize('limit, expected_calls, status', [(1, 1, 'error'), (2, 2, 'success'), (None, 2, 'success')])
@async_test
async def test_model_call_limit_from_toml_counts_requests_and_keeps_tool_effects(monkeypatch, tmp_path, limit, expected_calls, status):
    path = tmp_path / 'settings.toml'
    content = ('[[subagents]]\nname="tester"\ndescription="Verify"\nsystem_prompt="Prompt"\n'
               'tool_names=["probe"]\nmodel_id="B"\n')
    if limit is not None:
        content += f'max_model_calls={limit}\n'
    path.write_text(content)
    child = ChildModel(replies=[call('probe', {'value': 'actual'}, 'inner'), AIMessage(content='done')])
    effects = []
    probe = definition(calls=effects, approved=True, permissions=ToolPermission(default='allow'))
    app, root = await delegated_app(monkeypatch, child, settings=load_settings(path), tools=[probe])
    instances = record_instances(monkeypatch, app)
    result = await app.arun(await app.acreate_session('A'), [('user', 'verify')])
    feedback = next(message for message in result['messages'] if isinstance(message, ToolMessage))
    assert feedback.status == status and feedback.tool_call_id == 'delegate'
    assert len(child.seen) == expected_calls and len(root.seen) == 2
    assert effects == [('probe', 'actual')]
    if status == 'error':
        assert 'model call limit' in feedback.content.lower()
    instance, = instances
    snapshot = await app._sessions._subagents['tester']['agent'].aget_state(
        {'configurable': {'thread_id': instance['child_thread_id']}})
    if limit is not None:
        assert snapshot.values['thread_model_call_count'] == expected_calls


@async_test
async def test_same_role_parallel_instances_do_not_share_history_or_model_counts(monkeypatch):
    child = ChildModel(replies=[AIMessage(content='report one'), AIMessage(content='report two')])
    root = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': 'task one'}, 'id': 'one'},
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': 'task two'}, 'id': 'two'},
    ]), AIMessage(content='root done')])
    app, _ = await delegated_app(monkeypatch, child, root=root, spec=role(max_model_calls=1))
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_session('A')
    result = await app.arun(sid, [('user', 'two checks')])
    feedback = [message for message in result['messages'] if isinstance(message, ToolMessage)]
    assert [(message.tool_call_id, message.status) for message in feedback] == [('one', 'success'), ('two', 'success')]
    assert len({instance['child_thread_id'] for instance in instances}) == 2
    assert len({instance['agent_path'] for instance in instances}) == 2
    assert {next(message.content for message in seen if isinstance(message, HumanMessage)) for seen in child.seen} == {'task one', 'task two'}
    assert all(sum(isinstance(message, HumanMessage) for message in seen) == 1 for seen in child.seen)
    for instance in instances:
        snapshot = await app._sessions._subagents['tester']['agent'].aget_state(
            {'configurable': {'thread_id': instance['child_thread_id']}})
        assert snapshot.values['thread_model_call_count'] == 1


@async_test
async def test_inherited_child_model_is_frozen_when_root_model_switches(monkeypatch):
    switched = asyncio.Event()
    alternate = StreamingToolModel(replies=[AIMessage(content='root using B')])
    original = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'child task'}, 'delegate'),
                                   call('probe', {'value': 'read'}, 'inner'), AIMessage(content='child using A')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': original, 'B': alternate}))
    spec = SubAgentSpec('tester', 'Check', 'CHILD_ROLE_PROMPT', ('probe',))
    app = await build_application('unused', tools=[create_subagent_tool(), definition()], initial_active_tools=['subagent'],
                                  system_prompt='ROOT_PROMPT', response_format=None, settings=AppSettings(subagents=(spec,)))
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_session('A')
    async def switch_during_child(messages):
        if 'CHILD_ROLE_PROMPT' in messages[0].content and not switched.is_set():
            await app.aselect_model(sid, 'B')
            assert app._sessions.resolve_model(sid, instances[0]['agent_path'])[0] == 'A'
            switched.set()
    original.on_request = switch_during_child
    result = await app.arun(sid, [('user', 'start')])
    assert switched.is_set() and len(original.seen) == 3 and len(alternate.seen) == 1
    assert result['messages'][-1].content == 'root using B'
    instance, = instances
    assert instance['model_id'] == 'A' and app._sessions.resolve_model(sid)[0] == 'B'


@async_test
async def test_unknown_role_is_tool_error_and_does_not_start_child(monkeypatch):
    child = ChildModel(replies=[])
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'missing', 'task': 'task'}, 'delegate'), AIMessage(content='continue')])
    app, _ = await delegated_app(monkeypatch, child, root=root)
    result = await app.arun(await app.acreate_session('A'), [('user', 'start')])
    feedback = next(message for message in result['messages'] if isinstance(message, ToolMessage))
    assert feedback.status == 'error' and 'Unknown subagent' in feedback.content
    assert result['messages'][-1].content == 'continue' and child.seen == []
    assert all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values())


@async_test
async def test_leaf_tool_error_stays_in_child_history(monkeypatch, tmp_path):
    child = ChildModel(replies=[call('read', {'path': 'absent'}, 'inner'), AIMessage(content='file unavailable')])
    spec = SubAgentSpec('tester', 'Read', 'Prompt', ('read',), model_id='B')
    app, _ = await delegated_app(monkeypatch, child, spec=spec, tools=[create_session_read_tool()])
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'read')])
    assert next(message for message in child.seen[1] if isinstance(message, ToolMessage)).status == 'error'
    assert json.loads(next(message for message in result['messages'] if isinstance(message, ToolMessage)).content)['report'] == 'file unavailable'


@async_test
async def test_child_ask_keeps_checkpoint_and_never_executes_before_approval(monkeypatch):
    from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run
    effects = []
    child = ChildModel(replies=[call('probe', {'value': 'unapproved'}, 'inner'), AIMessage(content='approved report')])
    app, root = await delegated_app(monkeypatch, child, tools=[definition(calls=effects, approved=True)])
    instances = record_instances(monkeypatch, app)
    queue = approval_notifications(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'ask')]))
    try:
        batch = await next_approval(app, sid, queue)
        assert effects == [] and len(root.seen) == len(child.seen) == 1
        instance, = instances
        assert instance['status'] == 'awaiting_approval'
        snapshot = await app._sessions._subagents['tester']['agent'].aget_state(
            {'configurable': {'thread_id': instance['child_thread_id']}})
        assert any(item.interrupts for item in snapshot.tasks)
        assert app.pending_tool_approvals(sid) == []
        await submit(app, sid, batch)
        await asyncio.wait_for(task, 5)
        assert effects == [('probe', 'unapproved')] and instance['status'] == 'completed'
    finally:
        await cancel_run(task)
