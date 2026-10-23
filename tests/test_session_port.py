"""SessionPort is the real root/child assembly dependency with explicit scopes."""

from runtime_context_helpers import RunIdentity, typed_context
import asyncio
import inspect
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from native_application_helpers import async_test, build_application
from naiyue_test_agent import application, agent_runtime
from naiyue_test_agent.agent_runtime.tool_execution.permissions import fixed_permission_context
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import SubAgentSpec, create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel
from test_dynamic_tools import definition
from test_subagent_execution import ChildModel, record_instances


def test_protocol_names_the_existing_behavior_without_state_or_application_imports():
    from typing import get_type_hints
    from naiyue_test_agent.agent_runtime.session_port import SessionPort
    expected = {'resolve_request_configuration', 'resolve_model', 'resolve_active_tool_names', 'resolve_permission_context',
        'resolve_prompt_parts', 'get_registered_subagents', 'is_stop_requested',
        'drain_steering', 'finish_run_boundary', 'prepare_control_messages'}
    assert SessionPort._is_protocol
    assert {name for name, value in vars(SessionPort).items()
            if not name.startswith('_') and inspect.isfunction(value)} == expected
    for name in expected:
        assert callable(getattr(application.SessionService, name))
        get_type_hints(getattr(SessionPort, name))
    assert SessionPort not in application.SessionService.__mro__
    assert not any(name in vars(SessionPort) for name in ('state', 'storage', 'get_service'))


@async_test
async def test_application_passes_the_same_port_to_root_and_child_assembly(monkeypatch):
    from naiyue_test_agent.agent_runtime import subagents
    captured = []
    root_build, child_build = agent_runtime._assemble_interactive_runtime, subagents.build_subagent_runtime
    def capture_root(*args, **options):
        captured.append(('root', options))
        return root_build(*args, **options)
    def capture_child(*args, **options):
        captured.append(('child', options))
        return child_build(*args, **options)
    monkeypatch.setattr(agent_runtime, '_assemble_interactive_runtime', capture_root)
    monkeypatch.setattr(subagents, 'build_subagent_runtime', capture_child)
    root = StreamingToolModel(replies=[AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root}))
    app = await build_application('unused', tools=[create_subagent_tool()], system_prompt='', response_format=None,
        settings=AppSettings(subagents=(SubAgentSpec('tester', 'Check', 'Child', ()),)))
    assert {kind for kind, _ in captured} == {'root', 'child'}
    retired = {'resolve_model', 'resolve_active_tool_names', 'resolve_permission_context',
        'drain_steering', 'is_stop_requested', 'finish_run_boundary', 'prepare_control_messages',
        'resolve_project_instructions', 'resolve_subagent_roles', 'resolve_prompt_parts'}
    for _, options in captured:
        assert options['session_port'] is app._sessions
        assert not retired.intersection(options)
    sid = await app.acreate_session('A')
    assert (await app.arun(sid, [('user', 'run')]))['messages'][-1].content == 'done'


@async_test
async def test_interactive_graph_rejects_child_scope_without_consuming_root_controls():
    controls = []
    model = StreamingToolModel(replies=[])
    port = SimpleNamespace(
        resolve_model=lambda sid, path: ('A', model),
        resolve_request_configuration=lambda sid, path: ('A', model, ()),
        resolve_active_tool_names=lambda sid, path: (),
        resolve_permission_context=fixed_permission_context,
        resolve_prompt_parts=lambda sid, agent_path='root': [], get_registered_subagents=lambda: (),
        is_stop_requested=lambda sid: controls.append('stop') or False,
        drain_steering=lambda sid: controls.append('steering') or [],
        finish_run_boundary=lambda sid: controls.append('finish') or (False, []),
        prepare_control_messages=lambda sid, messages: [HumanMessage(content=text) for text in messages])
    graph = agent_runtime._assemble_interactive_runtime(model,
        tool_registry=application.prepare_tool_registry([]), session_port=port,
        system_prompt='', response_format=None, checkpointer=InMemorySaver())
    with pytest.raises(ValueError, match='requires root agent_path'):
        await graph.ainvoke({'messages': [('user', 'invalid root invocation')]},
            context=typed_context(identity=RunIdentity(session_id='s', agent_path='root/tester/child')),
            config={'configurable': {'thread_id': 'invalid-root'}})
    assert controls == [] and model.seen == []


@async_test
async def test_two_children_keep_scopes_while_root_switches_and_queues_controls(monkeypatch, tmp_path):
    root = ChildModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': 'task one'}, 'id': 'one'},
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': 'task two'}, 'id': 'two'}]),
        AIMessage(content='other session')])
    child = ChildModel(replies=[AIMessage(content='child report one'), AIMessage(content='child report two')])
    alternate = ChildModel(replies=[AIMessage(content='root continued'), AIMessage(content='root follow-up')])
    monkeypatch.setattr(application, 'load_model_catalog',
        lambda _: ModelCatalog('A', {'A': root, 'B': child, 'C': alternate}))
    (tmp_path / 'AGENTS.md').write_text('PROJECT_PORT_CONSTRAINT', encoding='utf-8')
    app = await build_application('unused', tools=[create_subagent_tool(), definition()],
        initial_active_tools=['subagent'], system_prompt='ROOT_PORT_PROMPT', response_format=None,
        settings=AppSettings(subagents=(SubAgentSpec('tester', 'Check', 'CHILD_PORT_PROMPT', ('probe',), model_id='B'),)))
    instances = record_instances(monkeypatch, app)
    sid = await app.acreate_workspace_session('A', tmp_path)
    other = await app.acreate_session('A')
    events, entered, release, paths = [], asyncio.Event(), asyncio.Event(), set()
    app.subscribe_events(events.append)
    async def wait_for_both(messages):
        from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
        paths.add(current_run_scope().observation.publisher.source.agent_path)
        if len(paths) == 2:
            entered.set()
        await release.wait()
    child.on_request = wait_for_both
    running = asyncio.create_task(app.arun(sid, [('user', 'delegate both')]))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await app.aselect_model(sid, 'C')
        await app.aset_active_tools(sid, ['probe'])
        await app.aset_permission_mode(sid, 'NoAsk')
        app.steer(sid, 'ROOT_STEERING_PORT')
        app.follow_up(sid, 'ROOT_FOLLOWUP_PORT')
        assert len(instances) == len(paths) == 2
        for path in paths:
            assert app._sessions.resolve_model(sid, path) == ('B', child)
            assert app._sessions.resolve_active_tool_names(sid, path) == ('probe',)
            assert app._sessions.resolve_permission_context(sid, path)['mode'] == 'NoAsk'
        assert app._sessions.resolve_model(sid)[0] == 'C'
        assert app._sessions.resolve_active_tool_names(sid) == ('probe',)
        assert app._sessions.resolve_model(other)[0] == 'A'
        assert app._sessions.resolve_active_tool_names(other) == ('subagent',)
        assert app.get_permission_mode(other) == 'Default'
        assert not any(e.event_type == 'control_delivered' for e in events)
        for resolve in (app._sessions.resolve_model, app._sessions.resolve_active_tool_names,
                        app._sessions.resolve_permission_context):
            with pytest.raises(KeyError):
                resolve(sid, 'root/tester/missing')
        release.set()
        result = await running
    finally:
        release.set()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert result['messages'][-1].content == 'root follow-up'
    assert child.bound == [('probe',), ('probe',)]
    assert alternate.bound == [('probe',), ('probe',)]
    assert all('CHILD_PORT_PROMPT' in messages[0].content and 'PROJECT_PORT_CONSTRAINT' in messages[0].content
               for messages in child.seen)
    assert all('ROOT_STEERING_PORT' not in str(messages) and 'ROOT_FOLLOWUP_PORT' not in str(messages)
               for messages in child.seen)
    assert 'ROOT_PORT_PROMPT' in alternate.seen[0][0].content
    assert 'PROJECT_PORT_CONSTRAINT' in alternate.seen[0][0].content
    assert any(m.content == 'ROOT_STEERING_PORT' for m in alternate.seen[0])
    assert any(m.content == 'ROOT_FOLLOWUP_PORT' for m in alternate.seen[1])
    assert {e.source.agent_path for e in events if e.source and e.event_type == 'model_call_started'} == paths
    delivered = [e for e in events if e.event_type == 'control_delivered']
    assert len(delivered) == 2 and all(e.source is None for e in delivered)
    assert (await app.arun(other, [('user', 'independent')]))['messages'][-1].content == 'other session'
    assert 'PROJECT_PORT_CONSTRAINT' not in root.seen[-1][0].content
