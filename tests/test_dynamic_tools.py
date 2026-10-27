"""One graph uses a session's selected definitions for each model request."""

from naiyue_test_agent.contracts.run_context import RuntimeContext

from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from native_application_helpers import async_test, build_application

import inspect
from typing import Any

import pytest
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent import application
from scripts.component_fixtures import build_component_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import ToolSpec, create_builtin_tools
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permissions import ToolPermission, require_tool_permit
from naiyue_test_agent.tools.schemas import build_tool_schemas


class DynamicModel(FakeMessagesListChatModel):
    bound: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)
    on_request: Any = None

    def bind_tools(self, tools, **kwargs):
        return self.bind(_selected_tools=list(tools))

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        self.bound.append(kwargs.pop('_selected_tools', []))
        if self.on_request:
            self.on_request(len(self.seen))
        return super()._generate(messages, *args, **kwargs)

    async def _agenerate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        self.bound.append(kwargs.pop('_selected_tools', []))
        if self.on_request:
            value = self.on_request(len(self.seen))
            if inspect.isawaitable(value):
                await value
        return super()._generate(messages, *args, **kwargs)


class ValueInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    value: str


def definition(name='probe', *, calls=None, approved=False, sequential=False, **kwargs):
    public, execution = build_tool_schemas('ValueInput', ValueInput)
    @tool(name, args_schema=execution)
    def probe(value: str, runtime: ToolRuntime[RuntimeContext]) -> str:
        """Return a supplied value with trusted context."""
        if approved:
            assert require_tool_permit(runtime)['args']['value'] == value
        if calls is not None:
            calls.append((name, value))
        return value
    return ToolSpec(probe, public, permissions=kwargs.pop('permissions', ToolPermission(default='ask' if approved else 'allow')), execution_mode='sequential' if sequential else 'parallel', **kwargs)


def call(name='probe', value='value', call_id='call'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': {'value': value}, 'id': call_id}])


async def build(monkeypatch, model, specs=(), initial=()):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    return await build_application('synthetic', tools=list(specs), initial_active_tools=list(initial),
        system_prompt='BASE', response_format=None)


@async_test
async def test_register_from_empty_graph_enable_one_session_and_preserve_original_identity(monkeypatch):
    calls = []
    model = DynamicModel(responses=[call(), AIMessage(content='done'), AIMessage(content='other')])
    app = await build(monkeypatch, model)
    graph = app.host.runs._agent
    first, second = await app.host.sessions.create('A'), await app.host.sessions.create('A')
    spec = definition(calls=calls)
    await app.host.tools.register(spec)
    assert app.host.sessions.active_tools(first) == app.host.sessions.active_tools(second) == ()
    assert app.host.sessions.active_tools(await app.host.sessions.create('A')) == ()
    await app.host.sessions.select_tools(first, ['probe'])
    result = await app.host.runs.start(first, [('user', 'run')])
    assert calls == [('probe', 'value')]
    assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).content == 'value'
    assert app.host.runs._agent is graph and model.bound[0][0] is spec.tool
    await app.host.runs.start(second, [('user', 'chat')])
    assert model.bound[-1] == []


@pytest.mark.parametrize('names', [['missing'], ['probe', 'probe'], 'probe'])
@async_test
async def test_invalid_selection_keeps_old_names(monkeypatch, names):
    app = await build(monkeypatch, DynamicModel(responses=[]), [definition()], ['probe'])
    sid = await app.host.sessions.create('A')
    with pytest.raises(ValueError):
        await app.host.sessions.select_tools(sid, names)
    assert app.host.sessions.active_tools(sid) == ('probe',)


@async_test
async def test_inactive_and_unknown_calls_never_execute_and_keep_events(monkeypatch):
    async def scenario():
        calls, events = [], []
        model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
            {'name': 'probe', 'args': {'value': 'PRIVATE'}, 'id': 'inactive'},
            {'name': 'missing', 'args': {'value': 'PRIVATE'}, 'id': 'unknown'}]), AIMessage(content='done')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_component_application('synthetic', tools=[definition(calls=calls)],
            initial_active_tools=[], system_prompt='BASE', response_format=None)
        sid = await app.host.sessions.create('A')
        app.host.observability.subscribe(events.append)
        try:
            result = await app.host.runs.start(sid, [('user', 'run')])
            feedback = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [m.tool_call_id for m in feedback] == ['inactive', 'unknown']
            assert all(m.status == 'error' and 'PRIVATE' not in m.content for m in feedback)
            assert calls == []
            finished = [e.data for e in events if e.event_type == 'tool_call_finished']
            assert len(finished) == 2 and all(item.outcome == 'not_executed' for item in finished)
        finally:
            await app.aclose()
    await scenario()


@async_test
async def test_switch_during_request_applies_only_to_next_request(monkeypatch):
    async def scenario():
        calls = []
        model = DynamicModel(responses=[call('old'), AIMessage(content='done')])
        specs = [definition('old', calls=calls), definition('new', calls=calls)]
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_component_application('synthetic', tools=specs, initial_active_tools=['old'],
            system_prompt='BASE', response_format=None)
        sid = await app.host.sessions.create('A')
        async def switch(number):
            if number == 1:
                await app.host.sessions.select_tools(sid, ['new'])
        model.on_request = switch
        try:
            result = await app.host.runs.start(sid, [('user', 'run')])
            assert result['messages'][-1].content == 'done'
            assert calls == [('old', 'value')]
            assert [[t.name for t in tools] for tools in model.bound] == [['old'], ['new']]
            assert 'old:' in model.seen[0][0].content and 'new:' not in model.seen[0][0].content
            assert 'new:' in model.seen[1][0].content and 'old:' not in model.seen[1][0].content
        finally:
            await app.aclose()
    await scenario()


@pytest.mark.parametrize('decision', ['approve', 'reject'])
@async_test
async def test_dynamic_approval_holds_whole_batch_and_resumes_saved_names(monkeypatch, tmp_path, decision):
    async def scenario():
        calls = []
        model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
            {'name': 'gated', 'args': {'value': 'review'}, 'id': 'gated'},
            {'name': 'plain', 'args': {'value': 'plain'}, 'id': 'plain'}]), AIMessage(content='done')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_component_application('synthetic', tools=[], initial_active_tools=[],
            system_prompt='BASE', response_format=None)
        sid = await app.host.sessions.create_workspace('A', tmp_path)
        for spec in [definition('gated', calls=calls, approved=True, sequential=True), definition('plain', calls=calls)]:
            await app.host.tools.register(spec)
        await app.host.sessions.select_tools(sid, ['gated', 'plain'])
        try:
            pending = await app.host.runs.start(sid, [('user', 'run')])
            assert calls == [] and len(pending['approval_requests']) == 1
            await app.host.sessions.select_tools(sid, [])
            result = await app.host.runs.resume_approval(sid, [decision])
            assert calls == ([('gated', 'review'), ('plain', 'plain')] if decision == 'approve' else [('plain', 'plain')])
            feedback = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [(m.tool_call_id, m.status) for m in feedback] == [
                ('gated', 'success' if decision == 'approve' else 'error'), ('plain', 'success')]
            assert model.bound[-1] == []
        finally:
            await app.aclose()
    await scenario()


@async_test
async def test_dynamic_runtime_validation_is_safe(monkeypatch, caplog):
    calls = []
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {'value': 42}, 'id': 'bad'}]), AIMessage(content='done')])
    app = await build(monkeypatch, model)
    sid = await app.host.sessions.create('A')
    await app.host.tools.register(definition(calls=calls))
    await app.host.sessions.select_tools(sid, ['probe'])
    result = await app.host.runs.start(sid, [('user', 'run')])
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert feedback.status == 'error' and calls == []
    diagnostics = feedback.content + ''.join(record.getMessage() for record in caplog.records)
    assert all(value not in diagnostics for value in ['runtime', 'workspace_root', '_run_control'])


@async_test
async def test_tool_prompt_guidelines_and_project_instructions_compose_once(monkeypatch, tmp_path):
    (tmp_path / 'AGENTS.md').write_text('PROJECT_INSTRUCTION')
    model = DynamicModel(responses=[call('first'), AIMessage(content='done')])
    first = definition('first', prompt_snippet='FIRST_SNIPPET', prompt_guidelines=('SHARED_GUIDE',))
    second = definition('second', prompt_guidelines=('SHARED_GUIDE', 'SECOND_GUIDE'))
    app = await build(monkeypatch, model, [first, second], ['first', 'second'])
    result = await app.host.runs.start(await app.host.sessions.create_workspace('A', tmp_path), [('user', 'run')])
    for messages in model.seen:
        prompt = messages[0].content
        assert prompt.count('PROJECT_INSTRUCTION') == prompt.count('SHARED_GUIDE') == 1
        assert 'FIRST_SNIPPET' in prompt and 'SECOND_GUIDE' in prompt
    assert not any('FIRST_SNIPPET' in str(m.content) for m in result['messages'])


def test_builtin_catalog_includes_bash_without_a_special_flag():
    specs = create_builtin_tools(AppSettings().tools)
    assert [spec.tool.name for spec in specs] == ['read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell', 'subagent', 'subagent_list', 'aitest_kit', 'web_search', 'web_fetch', 'task_list', 'task_output', 'task_stop', 'history', 'memory']


@pytest.mark.parametrize('field, value', [('prompt_snippet', 1), ('prompt_guidelines', ['guide']), ('prompt_guidelines', (1,))])
@async_test
async def test_registration_validates_tool_prompt_metadata(monkeypatch, field, value):
    app = await build(monkeypatch, DynamicModel(responses=[]))
    with pytest.raises(ValueError):
        await app.host.tools.register(definition(**{field: value}))
    assert app.host.tools.list() == ()


@async_test
async def test_registration_rejects_bad_permission_without_publishing(monkeypatch):
    app = await build(monkeypatch, DynamicModel(responses=[]))
    with pytest.raises(ValueError, match='permission'):
        await app.host.tools.register(definition(permissions=None))
    assert app.host.tools.list() == ()


@async_test
async def test_dynamic_approval_without_checkpointer_fails_before_execution(tmp_path):
    from naiyue_test_agent.agent_runtime import build_runtime_agent
    from naiyue_test_agent.tools import ToolRegistry
    registry, calls = ToolRegistry(), []
    model = DynamicModel(responses=[call('gated')])
    graph = build_runtime_agent(model, tool_registry=registry,
        resolve_active_tool_names=lambda _sid, agent_path='root': ('gated',),
        system_prompt='', response_format=None, resolve_model=lambda _, agent_path='root': ('A', model), )
    registry.register(definition('gated', approved=True, calls=calls))
    with pytest.raises(ValueError, match='checkpointer'):
        (await graph.ainvoke({'messages': [('user', 'run')]}, context=typed_context(identity=RunIdentity(session_id='s', execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None))))
    assert calls == []
