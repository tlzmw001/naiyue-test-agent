"""Role registration is application state, independent of Agent execution."""

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test, build_application
from naiyue_test_agent import application
from naiyue_test_agent.cli.cli_async import build_local_application
from naiyue_test_agent.model_access import ModelCatalog, ModelConfigError
from naiyue_test_agent.tools import SubAgentSpec
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel
from test_dynamic_tools import definition


def role(**changes):
    return SubAgentSpec(**{
        'name': 'tester', 'description': 'Run relevant tests.',
        'system_prompt': 'PRIVATE_ROLE_PROMPT', 'tool_names': ('probe',), **changes,
    })


@pytest.mark.parametrize('changes', [
    {'name': ''}, {'name': 'unsafe/name'}, {'name': 5},
    {'description': '  '}, {'description': None},
    {'system_prompt': ''}, {'system_prompt': 5},
    {'tool_names': ['probe']}, {'tool_names': 'probe'}, {'tool_names': ([],)},
    {'tool_names': ('',)}, {'tool_names': ('probe', 'probe')},
    {'tool_names': ('subagent',)}, {'model_id': ''}, {'model_id': True},
    {'max_model_calls': 0}, {'max_model_calls': -1},
    {'max_model_calls': True}, {'max_model_calls': 1.5}, {'max_model_calls': '2'},
])
def test_invalid_definition_is_rejected_at_shared_construction_boundary(changes):
    with pytest.raises(ValueError):
        role(**changes)


def test_definition_is_frozen_and_empty_tools_and_inheritance_are_valid():
    spec = role(tool_names=())
    assert spec.tool_names == () and spec.model_id is None and spec.max_model_calls is None
    with pytest.raises(FrozenInstanceError):
        spec.tool_names = ('other',)
    assert role(model_id='B', max_model_calls=2).max_model_calls == 2


@async_test
async def test_registration_preserves_identity_and_app_scope_without_activating_tools(monkeypatch):
    model = StreamingToolModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('unused', tools=[definition()], initial_active_tools=[],
                                  system_prompt='', response_format=None, settings=AppSettings())
    graph = app.runs._agent
    first, second = await app.sessions.create('A'), await app.sessions.create('A')
    spec, other = role(), role(name='researcher', tool_names=())
    assert app.subagents.list() == ()
    assert await app.subagents.register(spec) is None
    await app.subagents.register(other)
    registered = app.subagents.list()
    assert isinstance(registered, tuple) and registered == (spec, other)
    assert registered[0] is spec
    assert app.sessions.active_tools(first) == app.sessions.active_tools(second) == ()
    assert await app.history.messages(first) == await app.history.messages(second) == []
    assert app.runs.pending_tools(first) == app.runs.pending_tools(second) == []
    assert app.subagents.list() == registered and app.runs._agent is graph
    assert model.seen == [] and not app._lifetime._tasks
    isolated = await build_application('unused', tools=[], system_prompt='', response_format=None,
                                       settings=AppSettings())
    assert isolated.subagents.list() == ()


@pytest.mark.parametrize('bad, error_type', [
    ('wrong_type', ValueError), ('unknown_tool', ValueError),
    ('unknown_model', ModelConfigError), ('duplicate', ValueError),
])
@async_test
async def test_failed_registration_keeps_existing_definitions_and_safe_diagnostics(monkeypatch, caplog, bad, error_type):
    model = StreamingToolModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('unused', tools=[definition()], system_prompt='', response_format=None,
                                  settings=AppSettings())
    original = role()
    await app.subagents.register(original)
    invalid = {
        'wrong_type': {'system_prompt': 'PRIVATE_ROLE_PROMPT'},
        'unknown_tool': role(name='bad_tool', tool_names=('missing',)),
        'unknown_model': role(name='bad_model', model_id='missing'),
        'duplicate': replace(original, description='Changed description'),
    }[bad]
    with pytest.raises(error_type):
        await app.subagents.register(invalid)
    assert app.subagents.list() == (original,)
    assert app.subagents.list()[0] is original
    assert model.seen == []
    assert sum('subagent_registration_failed' in record.getMessage() for record in caplog.records) == 1
    assert 'PRIVATE_ROLE_PROMPT' not in caplog.text
    await app.subagents.register(role(name='corrected', model_id='A', max_model_calls=2))
    assert [item.name for item in app.subagents.list()] == ['tester', 'corrected']


@async_test
async def test_failed_registration_does_not_cancel_active_root_run(monkeypatch, caplog):
    entered, release = asyncio.Event(), asyncio.Event()
    model = StreamingToolModel(replies=[AIMessage(content='done')], entered=entered, release=release)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('unused', tools=[], system_prompt='', response_format=None,
                                  settings=AppSettings())
    sid = await app.sessions.create('A')
    events = []
    app.observability.subscribe(events.append)
    task = asyncio.create_task(app.runs.start(sid, [('user', 'root task')]))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(ValueError):
            await app.subagents.register(role(tool_names=('missing',)))
        assert not task.done() and app.sessions._sessions.require(sid).describe_session()['busy']
        assert app.subagents.list() == ()
        await app.subagents.register(role(tool_names=()))
        release.set()
        result = await task
        assert [message.content for message in result['messages']] == ['root task', 'done']
        assert len(model.seen) == 1 and not app.runs.pending_tools(sid)
        assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'completed'
        assert not any(event.event_type == 'tool_call_started' for event in events)
        assert 'PRIVATE_ROLE_PROMPT' not in caplog.text
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@async_test
async def test_registration_uses_owner_loop_and_rejects_closed_application(monkeypatch):
    model = StreamingToolModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('unused', tools=[], system_prompt='', response_format=None,
                                  settings=AppSettings())
    spec = role(tool_names=())

    async def foreign_loop():
        with pytest.raises(RuntimeError, match='owning event loop'):
            await app.subagents.register(spec)
        with pytest.raises(RuntimeError, match='owning event loop'):
            app.subagents.list()

    await asyncio.to_thread(lambda: asyncio.run(foreign_loop()))
    assert app.subagents.list() == ()
    await app.aclose()
    with pytest.raises(RuntimeError, match='open'):
        await app.subagents.register(spec)
    with pytest.raises(RuntimeError, match='open'):
        app.subagents.list()


def test_cli_registration_and_query_use_existing_event_loop_bridge(monkeypatch):
    model = StreamingToolModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = build_local_application('unused', tools=[definition()], initial_active_tools=[],
                                  system_prompt='', response_format=None, settings=AppSettings())
    spec = role()
    try:
        assert app.call(lambda api: api.subagents.register(spec)) is None
        assert app.call(lambda api: api.subagents.list()) == (spec,)
        assert app.call(lambda api: api.subagents.list())[0] is spec
        with pytest.raises(ValueError):
            app.call(lambda api: api.subagents.register(role(tool_names=('missing',))))
        assert app.call(lambda api: api.subagents.list()) == (spec,) and model.seen == []
    finally:
        app.close()
    assert not app._thread.is_alive()
